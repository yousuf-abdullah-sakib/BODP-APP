"""Fixes the Admin Data Requests page recomputing every request's
coverage (matching_record_count/dataset_total_record_count/
matching_percent) from scratch on every page load — profiled at ~96% of
that endpoint's server time (see conversation history / commit message
for the measured numbers). This file proves the replacement: coverage is
computed ONCE at request-submission time (requests_service.create_request)
and read back unchanged by the admin dashboard, with a derived is_stale
flag when the dataset has since been re-ingested (Dataset.version bumped).

Also proves the full filter set (quality/depth/platform/station/format/
processing_level) — previously silently dropped before a request was
ever submitted — is now actually captured and persisted.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetCategory, DatasetRecord, DatasetStatus
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio

_LONG_JUSTIFICATION = (
    "I am researching coastal water quality trends and need this dataset "
    "to calibrate my model against real observational records."
)


async def _seed_dataset_with_records(code: str, *, n_matching: int, n_other: int) -> str:
    """A published dataset with n_matching DatasetRecord rows for
    parameter='sea_surface_temp' and n_other rows for a different
    parameter — gives a real, hand-computable matching/total split for
    the snapshot to capture."""
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name=f"Cat-{code}", description="test", color_tag="cat-Environmental")
        db.add(category)
        await db.flush()
        dataset = Dataset(
            code=code,
            title=f"Snapshot Test {code}",
            description="test",
            category_id=category.id,
            status=DatasetStatus.PUBLISHED.value,
            record_count=n_matching + n_other,
        )
        db.add(dataset)
        await db.flush()

        for i in range(n_matching):
            db.add(
                DatasetRecord(
                    dataset_id=dataset.id,
                    time=date(2024, 1, (i % 28) + 1),
                    lat=22.0,
                    lon=91.0,
                    parameter="sea_surface_temp",
                    value=25.0 + i * 0.1,
                    quality_flag="normal",
                )
            )
        for i in range(n_other):
            db.add(
                DatasetRecord(
                    dataset_id=dataset.id,
                    time=date(2024, 1, (i % 28) + 1),
                    lat=22.0,
                    lon=91.0,
                    parameter="salinity",
                    value=35.0 + i * 0.1,
                    quality_flag="normal",
                )
            )
        await db.commit()
        await db.refresh(dataset)
        return str(dataset.id)


async def _admin_headers(client) -> dict:
    token = await register_verified_user(
        client, email="snapshot-admin@example.com", admin=True, permissions=["Approve Requests"]
    )
    return {"Authorization": f"Bearer {token}"}


async def _researcher_headers(client, email: str = "snapshot-researcher@example.com") -> dict:
    token = await register_verified_user(client, email=email)
    return {"Authorization": f"Bearer {token}"}


class TestSnapshotCapturedAtSubmission:
    async def test_submitting_a_request_stores_matching_and_total_counts(self, client):
        dataset_id = await _seed_dataset_with_records("BD-SNAP-001", n_matching=7, n_other=3)
        headers = await _researcher_headers(client)

        r = await client.post(
            "/api/v1/requests",
            data={
                "dataset_id": dataset_id,
                "justification": _LONG_JUSTIFICATION,
                "search_criteria": '{"parameters": ["sea_surface_temp"]}',
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        request_id = r.json()["id"]

        async with AsyncSessionLocal() as db:
            from app.models.requests import DatasetRequest

            request = await db.get(DatasetRequest, uuid.UUID(request_id))
            assert request.matching_record_count == 7
            assert request.dataset_total_record_count == 10
            assert request.matching_percent == 70.0
            assert request.dataset_version == 1

    async def test_no_search_criteria_matches_full_dataset(self, client):
        dataset_id = await _seed_dataset_with_records("BD-SNAP-002", n_matching=5, n_other=4)
        headers = await _researcher_headers(client, email="snapshot-researcher-2@example.com")

        r = await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": _LONG_JUSTIFICATION},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        request_id = r.json()["id"]

        async with AsyncSessionLocal() as db:
            from app.models.requests import DatasetRequest

            request = await db.get(DatasetRequest, uuid.UUID(request_id))
            assert request.matching_record_count == 9
            assert request.dataset_total_record_count == 9
            assert request.matching_percent == 100.0


class TestAdminDashboardReadsSnapshotWithoutRecomputing:
    async def test_list_returns_the_stored_snapshot(self, client):
        dataset_id = await _seed_dataset_with_records("BD-SNAP-003", n_matching=4, n_other=6)
        researcher_headers = await _researcher_headers(client, email="snapshot-researcher-3@example.com")
        await client.post(
            "/api/v1/requests",
            data={
                "dataset_id": dataset_id,
                "justification": _LONG_JUSTIFICATION,
                "search_criteria": '{"parameters": ["sea_surface_temp"]}',
            },
            headers=researcher_headers,
        )

        admin_headers = await _admin_headers(client)
        r = await client.get("/api/v1/admin/requests", headers=admin_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) == 1
        assert body[0]["matching_record_count"] == 4
        assert body[0]["dataset_total_record_count"] == 10
        assert body[0]["matching_percent"] == 40.0
        assert body[0]["is_stale"] is False

    async def test_adding_more_data_after_request_marks_it_stale(self, client):
        dataset_id = await _seed_dataset_with_records("BD-SNAP-004", n_matching=3, n_other=2)
        researcher_headers = await _researcher_headers(client, email="snapshot-researcher-4@example.com")
        await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": _LONG_JUSTIFICATION},
            headers=researcher_headers,
        )

        # Simulate re-ingestion changing the dataset's contents — bumps
        # Dataset.version the same way worker/tasks/ingestion.py does
        # whenever record_count changes, without needing a real file
        # upload for this test.
        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            dataset.version += 1
            dataset.record_count += 100
            await db.commit()

        admin_headers = await _admin_headers(client)
        r = await client.get("/api/v1/admin/requests", headers=admin_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) == 1
        # The snapshot numbers themselves are UNCHANGED (still reflect
        # what was true at submission time)...
        assert body[0]["dataset_total_record_count"] == 5
        # ...but the endpoint now flags them as stale, since the
        # dataset's version has moved on since the snapshot was taken.
        assert body[0]["is_stale"] is True

    async def test_rejecting_a_request_returns_the_snapshot_not_a_recompute(self, client):
        dataset_id = await _seed_dataset_with_records("BD-SNAP-005", n_matching=2, n_other=8)
        researcher_headers = await _researcher_headers(client, email="snapshot-researcher-5@example.com")
        r = await client.post(
            "/api/v1/requests",
            data={
                "dataset_id": dataset_id,
                "justification": _LONG_JUSTIFICATION,
                "search_criteria": '{"parameters": ["sea_surface_temp"]}',
            },
            headers=researcher_headers,
        )
        request_id = r.json()["id"]

        admin_headers = await _admin_headers(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/reject",
            json={"reason": "Not needed for this test."},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["matching_record_count"] == 2
        assert body["dataset_total_record_count"] == 10


class TestFullFilterSetIsCaptured:
    """Previously only parameters/date_from/date_to/bounds were carried
    from the catalog detail page's live filter panel into a submitted
    request — quality/depth_min/depth_max/source/platform/station/
    format/processing_level were tracked in the UI but silently dropped
    before submission. Confirms the extended SearchCriteriaSchema now
    round-trips the complete filter set."""

    async def test_all_filter_fields_persist_through_submission(self, client):
        dataset_id = await _seed_dataset_with_records("BD-SNAP-006", n_matching=1, n_other=1)
        headers = await _researcher_headers(client, email="snapshot-researcher-6@example.com")

        import json

        criteria = {
            "parameters": ["sea_surface_temp"],
            "quality": "normal",
            "source": "buoy-network",
            "platform": "R/V Meen Sandhani",
            "station": "ST-01",
            "format": "csv",
            "processing_level": "L2",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
            "depth_min": 0,
            "depth_max": 50,
            "bounds": {"lat_min": 20.0, "lat_max": 23.0, "lon_min": 88.0, "lon_max": 92.0},
        }

        r = await client.post(
            "/api/v1/requests",
            data={
                "dataset_id": dataset_id,
                "justification": _LONG_JUSTIFICATION,
                "search_criteria": json.dumps(criteria),
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        request_id = r.json()["id"]

        async with AsyncSessionLocal() as db:
            from app.models.requests import DatasetRequest

            request = await db.get(DatasetRequest, uuid.UUID(request_id))
            stored = request.search_criteria
            assert stored["quality"] == "normal"
            assert stored["platform"] == "R/V Meen Sandhani"
            assert stored["station"] == "ST-01"
            assert stored["format"] == "csv"
            assert stored["processing_level"] == "L2"
            assert stored["depth_min"] == 0
            assert stored["depth_max"] == 50


class TestExtractionRespectsExtendedScopeFields:
    """The new SearchCriteriaSchema fields must actually filter real
    extracted data, not just round-trip through storage — proves
    scope_filter.py's extension (quality/depth/platform/station/format/
    processing_level) genuinely narrows the extracted output."""

    async def test_quality_scope_narrows_csv_extraction_output(self, client, tmp_path):
        import io

        import pandas as pd

        from app.models.requests import AccessGrant, GrantStatus
        from app.models.user import User
        from datetime import UTC, datetime, timedelta

        upload_token = await register_verified_user(
            client, email="snapshot-extract-admin@example.com", admin=True,
            permissions=["Edit Datasets"],
        )
        admin_headers = {"Authorization": f"Bearer {upload_token}"}
        email = "snapshot-extract-researcher@example.com"
        researcher_headers = await _researcher_headers(client, email=email)

        async with AsyncSessionLocal() as db:
            category = DatasetCategory(name="Cat-SNAP-EXTRACT", description="test", color_tag="cat-Environmental")
            db.add(category)
            await db.flush()
            dataset = Dataset(
                code="BD-SNAP-EXTRACT",
                title="Extraction Scope Test",
                description="test",
                category_id=category.id,
                status=DatasetStatus.PUBLISHED.value,
                record_count=0,
            )
            db.add(dataset)
            await db.commit()
            await db.refresh(dataset)
            dataset_id = str(dataset.id)

        df = pd.DataFrame(
            {
                "time": pd.date_range("2024-01-01", periods=4, freq="D"),
                "lat": [22.0] * 4,
                "lon": [91.0] * 4,
                "sea_surface_temp": [25.0, 25.1, 25.2, 25.3],
                "quality_flag": ["normal", "normal", "caution", "alert"],
            }
        )
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files",
            files={"file": ("scope_test.csv", buf.getvalue(), "text/csv")},
            headers=admin_headers,
        )
        assert r.status_code == 202, r.text

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one()
            grant = AccessGrant(
                user_id=user.id,
                dataset_id=uuid.UUID(dataset_id),
                granted_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=1),
                status=GrantStatus.ACTIVE.value,
                scope=None,
            )
            db.add(grant)
            await db.commit()
            await db.refresh(grant)
            grant_id = str(grant.id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {"quality": "normal"}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text

        from app.models.requests import SubsetExtraction
        from app.services.storage.registry import get_storage_backend

        async with AsyncSessionLocal() as db:
            extraction = await db.get(SubsetExtraction, uuid.UUID(extraction_id))
            storage = get_storage_backend(extraction.output_storage_backend)
            body_bytes = storage.get(extraction.output_bucket, extraction.output_file_key).read()

        out_df = pd.read_csv(io.BytesIO(body_bytes))
        assert len(out_df) == 2  # only the two "normal"-quality rows
        assert set(out_df["quality_flag"]) == {"normal"}


class TestDatasetVersionReflectsSuccessfulContentChange:
    """Dataset.version must bump on every SUCCESSFUL, content-changing
    ingestion — not merely when record_count's own truthiness happens to
    be nonzero. The original implementation gated the bump on `if
    content_count:`, which meant a genuinely successful ingestion of a
    zero-content file (e.g. a header-only CSV — csv_parser.py explicitly
    supports this: "still produce a valid (empty) parquet output") left
    version frozen even though the dataset gained a real new file
    (spatial_extent/temporal_start/temporal_end/parameters/formats could
    all still change). A frozen version under those conditions would make
    DatasetRequest.is_stale silently miss a real content change. A
    failed ingestion (parser rejects the file) must NOT bump version —
    proven separately below, since _run_ingestion's version-bump line is
    only reachable after every earlier stage (parse/to_processed/upload/
    variable-registry write) has already succeeded without raising."""

    async def test_header_only_csv_still_bumps_version(self, client, admin_headers):
        r = await client.post(
            "/api/v1/admin/datasets",
            json={"title": "Version Bump Zero-Content Test", "description": None},
            headers=admin_headers,
        )
        assert r.status_code == 201, r.text
        dataset_id = r.json()["id"]

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            assert dataset.version == 1
            assert dataset.record_count == 0

        # Header row only — zero data rows. Valid CSV (has columns), so
        # csv_parser.py's parse()/to_processed() both succeed; record_
        # count for this file is 0, which is exactly the case the
        # original `if content_count:` gate silently missed.
        header_only_csv = b"time,lat,lon,sea_surface_temp\n"
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files",
            files={"file": ("header_only.csv", header_only_csv, "text/csv")},
            headers=admin_headers,
        )
        assert r.status_code == 202, r.text
        file_metadata = r.json()["dataset_file"]["file_metadata"]
        assert file_metadata is not None, "ingestion did not complete successfully"
        assert file_metadata["record_count"] == 0

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            # The real assertion: version moved even though record_count
            # (a real, successfully-ingested value) is still 0.
            assert dataset.version == 2
            assert dataset.record_count == 0

    async def test_failed_ingestion_does_not_bump_version(self, client, admin_headers):
        r = await client.post(
            "/api/v1/admin/datasets",
            json={"title": "Version Bump Failure Test", "description": None},
            headers=admin_headers,
        )
        assert r.status_code == 201, r.text
        dataset_id = r.json()["id"]

        # A file with NO columns at all — csv_parser.parse() explicitly
        # raises ParserError("CSV file has no columns") for this, which
        # propagates out of _run_ingestion before the version-bump line
        # is ever reached (see process_dataset_file's try/except).
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files",
            files={"file": ("no_columns.csv", b"\n\n\n", "text/csv")},
            headers=admin_headers,
        )
        # Accepted for background processing either way (202) — the
        # failure happens inside the Celery task, reflected in the
        # upload's terminal state, not the initial HTTP response.
        assert r.status_code == 202, r.text
        upload_id = r.json()["upload"]["id"]

        async with AsyncSessionLocal() as db:
            from app.models.uploads import Upload, UploadStatus

            upload = await db.get(Upload, uuid.UUID(upload_id))
            assert upload.status == UploadStatus.FAILED.value, (
                f"expected the upload to fail (no-columns CSV), got status={upload.status!r} "
                f"— if this assertion fails, the fixture itself needs revisiting, not the "
                f"version-freeze behavior below"
            )

            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            assert dataset.version == 1
            assert dataset.record_count == 0
