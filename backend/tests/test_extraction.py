import io
import uuid
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetCategory, DatasetStatus
from app.models.requests import AccessGrant, DownloadLog, ExtractionStatus, GrantStatus
from app.models.user import User
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


def _make_csv_bytes(*, n: int = 30, start: str = "2023-01-01") -> bytes:
    df = pd.DataFrame(
        {
            "time": pd.date_range(start, periods=n, freq="D"),
            "lat": [22.0 + i * 0.01 for i in range(n)],
            "lon": [91.0 + i * 0.01 for i in range(n)],
            "sea_surface_temp": [27.0 + i * 0.05 for i in range(n)],
        }
    )
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


async def _admin_headers(client) -> dict:
    token = await register_verified_user(
        client,
        email="extract-admin@example.com",
        admin=True,
        permissions=["Approve Requests", "Edit Datasets"],
    )
    return {"Authorization": f"Bearer {token}"}


async def _researcher(client, email: str) -> dict:
    """Registers a researcher user and returns their auth headers. The
    caller keeps `email` around separately for _approved_grant() — looking
    the user up by a LIKE pattern instead would be ambiguous whenever a test
    registers more than one researcher (ownership tests all do)."""
    token = await register_verified_user(client, email=email)
    return {"Authorization": f"Bearer {token}"}


async def _dataset_with_real_file(client, admin_headers, *, code: str, csv_bytes: bytes) -> str:
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name=f"Cat-{code}", description="test", color_tag="cat-Environmental")
        db.add(category)
        await db.flush()
        dataset = Dataset(
            code=code,
            title=f"Extraction Test Dataset {code}",
            description="test",
            category_id=category.id,
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.commit()
        await db.refresh(dataset)
        dataset_id = str(dataset.id)

    files = {"file": (f"{code}.csv", csv_bytes, "text/csv")}
    r = await client.post(
        f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
    )
    assert r.status_code == 202, r.text
    return dataset_id


async def _approved_grant(
    client, admin_headers, researcher_email: str, *, dataset_id: str, scope: dict | None = None
) -> str:
    """Bypasses the request/approve HTTP flow (already covered by
    test_requests.py) and creates an AccessGrant directly, since this
    module's focus is extraction behavior given a grant, not how grants
    come to exist."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == researcher_email))
        user = result.scalar_one()
        grant = AccessGrant(
            user_id=user.id,
            dataset_id=uuid.UUID(dataset_id),
            granted_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=30),
            status=GrantStatus.ACTIVE.value,
            scope=scope,
        )
        db.add(grant)
        await db.commit()
        await db.refresh(grant)
        return str(grant.id)


class TestExtractionCreate:
    async def test_extract_small_file_completes_synchronously(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-sync@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-SYNC", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        body = r.json()
        assert body["status"] == "complete"
        assert body["output_size_bytes"] and body["output_size_bytes"] > 0

    async def test_extract_narrows_output_to_requested_scope(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-scope@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-SCOPE", csv_bytes=_make_csv_bytes(n=30, start="2023-01-01")
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {"date_from": "2023-01-15"}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete"

        dl = await client.get(
            f"/api/v1/me/extractions/{extraction_id}/download", headers=researcher_headers
        )
        assert dl.status_code == 200
        assert "download_url" in dl.json()

    async def test_extract_rejects_scope_outside_grant_date_range(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-oob-date@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-OOB-DATE", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(
            client,
            admin_headers,
            email,
            dataset_id=dataset_id,
            scope={"date_from": "2023-01-05", "date_to": "2023-01-10"},
        )

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {"date_from": "2023-01-01"}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code == 422

    async def test_extract_rejects_scope_outside_grant_bounds(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-oob-bbox@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-OOB-BBOX", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(
            client,
            admin_headers,
            email,
            dataset_id=dataset_id,
            scope={"bounds": {"lat_min": 21.0, "lat_max": 22.0, "lon_min": 90.0, "lon_max": 91.0}},
        )

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={
                "scope": {"bounds": {"lat_min": 20.0, "lat_max": 23.0, "lon_min": 89.0, "lon_max": 92.0}},
                "format": "csv",
            },
            headers=researcher_headers,
        )
        assert r.status_code == 422

    async def test_extract_allows_scope_matching_category(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-cat-match@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-CAT-OK", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(
            client, admin_headers, email, dataset_id=dataset_id, scope={"category": "Environmental"}
        )

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {"category": "Environmental"}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text

    async def test_extract_rejects_mismatched_category(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-cat-bad@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-CAT-BAD", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(
            client, admin_headers, email, dataset_id=dataset_id, scope={"category": "Environmental"}
        )

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {"category": "Pollution"}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code == 422

    async def test_extract_allows_any_scope_when_grant_scope_is_none(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-none-scope@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-NONE-SCOPE", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id, scope=None)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={
                "scope": {"bounds": {"lat_min": -90, "lat_max": 90, "lon_min": -180, "lon_max": 180}},
                "format": "csv",
            },
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text

    async def test_extract_rejected_for_expired_grant(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-expired@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-EXPIRED", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        async with AsyncSessionLocal() as db:
            grant = await db.get(AccessGrant, uuid.UUID(grant_id))
            grant.expires_at = datetime.now(UTC) - timedelta(days=1)
            await db.commit()

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code == 409

    async def test_extract_rejected_for_revoked_grant(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-revoked@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-REVOKED", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        async with AsyncSessionLocal() as db:
            grant = await db.get(AccessGrant, uuid.UUID(grant_id))
            grant.status = GrantStatus.REVOKED.value
            await db.commit()

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code == 409

    async def test_cannot_extract_from_non_owned_grant(self, client):
        admin_headers = await _admin_headers(client)
        owner_email = "extract-researcher-owner@example.com"
        await _researcher(client, owner_email)
        other_headers = await _researcher(client, "extract-researcher-nonowner@example.com")
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-OWNERSHIP", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, owner_email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "csv"},
            headers=other_headers,
        )
        assert r.status_code == 404


class TestExtractionDownload:
    async def test_download_writes_one_log_row_per_call(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-dl@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-DL", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "csv"},
            headers=researcher_headers,
        )
        extraction_id = r.json()["id"]

        await client.get(f"/api/v1/me/extractions/{extraction_id}/download", headers=researcher_headers)
        await client.get(f"/api/v1/me/extractions/{extraction_id}/download", headers=researcher_headers)

        async with AsyncSessionLocal() as db:
            logs = (
                await db.execute(
                    select(DownloadLog).where(DownloadLog.subset_extraction_id == uuid.UUID(extraction_id))
                )
            ).scalars().all()
            assert len(logs) == 2

    async def test_download_404s_for_non_owning_user(self, client):
        admin_headers = await _admin_headers(client)
        owner_email = "extract-researcher-dl-owner@example.com"
        researcher_headers = await _researcher(client, owner_email)
        other_headers = await _researcher(client, "extract-researcher-dl-nonowner@example.com")
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-DL-OWNERSHIP", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, owner_email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "csv"},
            headers=researcher_headers,
        )
        extraction_id = r.json()["id"]

        r2 = await client.get(
            f"/api/v1/me/extractions/{extraction_id}/download", headers=other_headers
        )
        assert r2.status_code == 404

    async def test_download_409s_before_extraction_completes(self, client):
        """Directly constructs a queued (not-yet-complete) extraction row to
        assert the download endpoint's readiness gate, without depending on
        timing around the real async pipeline."""
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-notready@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-NOTREADY", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        from app.models.requests import SubsetExtraction

        async with AsyncSessionLocal() as db:
            extraction = SubsetExtraction(
                grant_id=uuid.UUID(grant_id), status=ExtractionStatus.QUEUED.value, format="csv"
            )
            db.add(extraction)
            await db.commit()
            await db.refresh(extraction)
            extraction_id = str(extraction.id)

        r = await client.get(
            f"/api/v1/me/extractions/{extraction_id}/download", headers=researcher_headers
        )
        assert r.status_code == 409


class TestGrantRevocationInvalidatesExtraction:
    async def test_revoke_fails_in_flight_extraction(self, client):
        admin_headers = await _admin_headers(client)
        email = "extract-researcher-revoke-inflight@example.com"
        await _researcher(client, email)
        dataset_id = await _dataset_with_real_file(
            client, admin_headers, code="BD-EXT-REVOKE-INFLIGHT", csv_bytes=_make_csv_bytes(n=10)
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        from app.models.requests import SubsetExtraction

        async with AsyncSessionLocal() as db:
            extraction = SubsetExtraction(
                grant_id=uuid.UUID(grant_id), status=ExtractionStatus.PROCESSING.value, format="csv"
            )
            db.add(extraction)
            await db.commit()
            await db.refresh(extraction)
            extraction_id = extraction.id

        r = await client.post(f"/api/v1/admin/grants/{grant_id}/revoke", headers=admin_headers)
        assert r.status_code == 200, r.text

        async with AsyncSessionLocal() as db:
            extraction = await db.get(SubsetExtraction, extraction_id)
            assert extraction.status == ExtractionStatus.FAILED.value
            assert "revoked" in extraction.error_message.lower()
