"""PLAN.md Phase 5 (Storage & Query Architecture) — proves admin_qc_
service's three detectors (duplicate/outlier/missing-values) find real,
seeded anomalies in a PARQUET-backed DatasetFile, closing the blind spot
new tabular ingestion would otherwise silently create (see PLAN.md's
"Admin QC scanning" item — this was found to be a real gap during
research, not a hypothetical one). Mirrors test_admin_qc.py's existing
DatasetRecord-based fixture pattern, but uploads a real CSV through the
HTTP ingestion pipeline instead of seeding DatasetRecord rows directly,
since Phase 5 ingestion never writes those rows anymore.
"""

import io

import pandas as pd
import pytest

from app.models.uploads import QualityIssueType
from tests.conftest import register_verified_user
from tests.test_dataset_upload import _create_dataset

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"qc5-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


def _make_anomalous_csv_bytes() -> bytes:
    """20 tightly-clustered temperature rows + 1 extreme outlier (9999) +
    2 exact-duplicate salinity rows (same time/station) — same anomaly
    shapes as test_admin_qc.py's DatasetRecord fixture, translated into a
    tidy CSV a real upload turns into Parquet."""
    rows = []
    for i in range(20):
        rows.append(
            {
                "time": f"2025-01-{(i % 28) + 1:02d}",
                "lat": 22.0,
                "lon": 91.0,
                "station": "QC-STATION",
                "temperature": 25.0 + (i % 3) * 0.1,
                "salinity": None,
            }
        )
    rows.append(
        {
            "time": "2025-01-15",
            "lat": 22.0,
            "lon": 91.0,
            "station": "QC-STATION",
            "temperature": 9999.0,
            "salinity": None,
        }
    )
    for _ in range(2):
        rows.append(
            {
                "time": "2025-02-01",
                "lat": 22.0,
                "lon": 91.0,
                "station": "QC-STATION",
                "temperature": None,
                "salinity": 35.0,
            }
        )
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


async def _upload_anomalous_dataset(client, admin_headers) -> str:
    dataset_id = await _create_dataset(client, admin_headers)
    files = {"file": ("qc_phase5.csv", _make_anomalous_csv_bytes(), "text/csv")}
    r = await client.post(
        f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
    )
    assert r.status_code == 202, r.text
    assert r.json()["dataset_file"]["file_metadata"]["storage_kind"] == "parquet"
    return dataset_id


class TestQcScanParquetBacked:
    async def test_scan_detects_all_three_issue_types_for_parquet_file(self, client, admin_headers):
        qc_headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _upload_anomalous_dataset(client, admin_headers)

        # Ingestion sets dataset.record_count from the parser's own
        # metadata (23 source rows here) — declared-vs-actual is the
        # missing-values signal, but a genuinely complete Parquet file
        # wouldn't trip it on its own; inflate the declared count
        # in-place to reproduce the same "expected more than we have"
        # scenario test_admin_qc.py's fixture manufactures directly.
        import uuid as uuid_module

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import Dataset

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid_module.UUID(dataset_id))
            dataset.record_count = 100
            await db.commit()

        r = await client.post("/api/v1/admin/qc/scan", headers=qc_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        found_types = {i["issue_type"] for i in body["issues"] if i["dataset_id"] == dataset_id}
        assert found_types == {
            QualityIssueType.OUTLIERS.value,
            QualityIssueType.DUPLICATE_RECORDS.value,
            QualityIssueType.MISSING_VALUES.value,
        }

    async def test_rescan_does_not_duplicate_open_issues_for_parquet_file(self, client, admin_headers):
        qc_headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _upload_anomalous_dataset(client, admin_headers)

        r = await client.post("/api/v1/admin/qc/scan", headers=qc_headers)
        assert r.status_code == 200, r.text
        first_scan_dataset_issues = [
            i for i in r.json()["issues"] if i["dataset_id"] == dataset_id
        ]
        assert len(first_scan_dataset_issues) >= 2  # duplicates + outliers, at minimum

        r = await client.post("/api/v1/admin/qc/scan", headers=qc_headers)
        second_scan_dataset_issues = [
            i for i in r.json()["issues"] if i["dataset_id"] == dataset_id
        ]
        assert second_scan_dataset_issues == []

    async def test_clean_parquet_file_produces_no_issues(self, client, admin_headers):
        """A Parquet file with no anomalies must not be flagged — same
        no-false-positive requirement the legacy DatasetRecord detectors
        already satisfy."""
        qc_headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _create_dataset(client, admin_headers)

        df = pd.DataFrame(
            {
                "time": pd.date_range("2024-01-01", periods=5, freq="D"),
                "lat": [22.0] * 5,
                "lon": [91.0] * 5,
                "station": [f"ST-{i}" for i in range(5)],
                "temperature": [25.0, 25.1, 25.2, 25.1, 25.0],
            }
        )
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        files = {"file": ("qc_clean.csv", buf.getvalue(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        r = await client.post("/api/v1/admin/qc/scan", headers=qc_headers)
        assert r.status_code == 200, r.text
        dataset_issues = [i for i in r.json()["issues"] if i["dataset_id"] == dataset_id]
        assert dataset_issues == []
