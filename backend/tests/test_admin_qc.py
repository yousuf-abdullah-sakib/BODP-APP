import uuid
from datetime import date

import pytest

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetRecord, DatasetStatus
from app.models.uploads import QualityIssueType
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"qc-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_dataset_with_anomalies() -> str:
    async with AsyncSessionLocal() as db:
        dataset = Dataset(
            code="BD-QC-001",
            title="QC Anomaly Dataset",
            status=DatasetStatus.DRAFT.value,
            # Declared far higher than what we'll actually insert, to
            # trigger the missing-values check.
            record_count=100,
        )
        db.add(dataset)
        await db.flush()

        # A tight cluster of normal values plus one extreme outlier.
        for i in range(20):
            db.add(
                DatasetRecord(
                    dataset_id=dataset.id,
                    time=date(2025, 1, i % 28 + 1),
                    lat=22.0,
                    lon=91.0,
                    parameter="temperature",
                    value=25.0 + (i % 3) * 0.1,
                )
            )
        db.add(
            DatasetRecord(
                dataset_id=dataset.id,
                time=date(2025, 1, 15),
                lat=22.0,
                lon=91.0,
                parameter="temperature",
                value=9999.0,
            )
        )

        # An exact duplicate (same time/station_id/parameter) pair.
        dup_time = date(2025, 2, 1)
        for _ in range(2):
            db.add(
                DatasetRecord(
                    dataset_id=dataset.id,
                    time=dup_time,
                    lat=22.0,
                    lon=91.0,
                    parameter="salinity",
                    value=35.0,
                )
            )

        await db.commit()
        return str(dataset.id)


class TestQcScan:
    async def test_scan_detects_all_three_issue_types(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        await _seed_dataset_with_anomalies()

        r = await client.post("/api/v1/admin/qc/scan", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["issues_found"] == 3
        found_types = {i["issue_type"] for i in body["issues"]}
        assert found_types == {
            QualityIssueType.OUTLIERS.value,
            QualityIssueType.DUPLICATE_RECORDS.value,
            QualityIssueType.MISSING_VALUES.value,
        }

    async def test_rescan_does_not_duplicate_open_issues(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        await _seed_dataset_with_anomalies()

        r = await client.post("/api/v1/admin/qc/scan", headers=headers)
        assert r.json()["issues_found"] == 3

        r = await client.post("/api/v1/admin/qc/scan", headers=headers)
        assert r.json()["issues_found"] == 0

        r = await client.get("/api/v1/admin/qc/issues", headers=headers)
        assert len(r.json()) == 3

    async def test_scan_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Users"])
        r = await client.post("/api/v1/admin/qc/scan", headers=headers)
        assert r.status_code == 403

    async def test_resolve_and_ignore_issue(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        await _seed_dataset_with_anomalies()
        r = await client.post("/api/v1/admin/qc/scan", headers=headers)
        issue_id = r.json()["issues"][0]["id"]

        r = await client.patch(
            f"/api/v1/admin/qc/issues/{issue_id}", json={"status": "resolved"}, headers=headers
        )
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"

        r = await client.get("/api/v1/admin/qc/issues?status_filter=open", headers=headers)
        assert all(i["id"] != issue_id for i in r.json())
