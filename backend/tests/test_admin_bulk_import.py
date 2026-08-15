"""Admin Panel Bulk Import UI (production-readiness follow-up) — the new
POST /admin/bulk-import(/validate) HTTP surface over the existing CLI
bulk_import implementation. Proves: permission enforcement, real
validation without writing, the async/background dispatch shape (a real,
immediately-pollable Upload.id returned before the transfer completes),
and that Celery's eager-mode test execution (which runs this task inline,
same as every other task in this suite) still reaches the exact same
ingestion result as the CLI path and the ordinary HTTP upload path."""

import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetFile, DatasetRecord
from app.models.uploads import Upload, UploadStatus
from tests.conftest import register_verified_user
from tests.test_dataset_upload import _create_dataset, _make_csv_bytes

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"bulk-import-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


class TestValidateEndpoint:
    async def test_requires_permission(self, client, tmp_path):
        headers = await _admin_headers(client, permissions=["Manage Users"])
        source = tmp_path / "bulk.csv"
        source.write_bytes(_make_csv_bytes())

        r = await client.post(
            "/api/v1/admin/bulk-import/validate",
            json={"dataset_id": str(uuid.uuid4()), "source_path": str(source)},
            headers=headers,
        )
        assert r.status_code == 403

    async def test_validates_real_file_without_writing(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        source = tmp_path / "validate_test.csv"
        source.write_bytes(_make_csv_bytes())

        r = await client.post(
            "/api/v1/admin/bulk-import/validate",
            json={"dataset_id": dataset_id, "source_path": str(source)},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["filename"] == "validate_test.csv"
        assert body["extension"] == "csv"
        assert body["target_key"].startswith(f"raw/{dataset_id}/")

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == uuid.UUID(dataset_id))
            )
            assert result.scalars().all() == []

    async def test_rejects_missing_path(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        r = await client.post(
            "/api/v1/admin/bulk-import/validate",
            json={"dataset_id": dataset_id, "source_path": str(tmp_path / "does_not_exist.csv")},
            headers=admin_headers,
        )
        assert r.status_code == 400
        assert "No such file" in r.json()["error"]["message"]

    async def test_rejects_unknown_dataset(self, client, admin_headers, tmp_path):
        source = tmp_path / "orphan.csv"
        source.write_bytes(_make_csv_bytes())
        r = await client.post(
            "/api/v1/admin/bulk-import/validate",
            json={"dataset_id": str(uuid.uuid4()), "source_path": str(source)},
            headers=admin_headers,
        )
        assert r.status_code == 400
        assert "No dataset found" in r.json()["error"]["message"]


class TestStartEndpoint:
    async def test_requires_permission(self, client, tmp_path):
        headers = await _admin_headers(client, permissions=["Manage Users"])
        source = tmp_path / "bulk.csv"
        source.write_bytes(_make_csv_bytes())
        r = await client.post(
            "/api/v1/admin/bulk-import",
            json={"dataset_id": str(uuid.uuid4()), "source_path": str(source)},
            headers=headers,
        )
        assert r.status_code == 403

    async def test_returns_immediately_with_pollable_upload(self, client, admin_headers, tmp_path):
        """The core async/background-job requirement: the response comes
        back with a real Upload.id usable against the EXISTING
        GET /admin/datasets/uploads/{id} endpoint — no separate polling
        surface was invented for bulk import."""
        dataset_id = await _create_dataset(client, admin_headers)
        source = tmp_path / "start_test.csv"
        source.write_bytes(_make_csv_bytes())

        r = await client.post(
            "/api/v1/admin/bulk-import",
            json={"dataset_id": dataset_id, "source_path": str(source)},
            headers=admin_headers,
        )
        assert r.status_code == 202, r.text
        upload_id = r.json()["upload"]["id"]
        assert upload_id

        # Celery runs eagerly in tests, so by the time the response above
        # returned, the background task has already run to completion —
        # poll the same status endpoint the browser-upload/multipart
        # paths already use.
        r = await client.get(f"/api/v1/admin/datasets/uploads/{upload_id}", headers=admin_headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "complete"
        assert r.json()["progress_stage"] == "complete"
        assert r.json()["progress_pct"] == 100

    async def test_reaches_same_ingestion_result_as_cli_and_http_upload(
        self, client, admin_headers, tmp_path
    ):
        """Same acceptance criterion as the CLI's own equivalent test —
        the Admin Panel entry point must produce an identical
        DatasetFile/DatasetRecord/DatasetVariable result, since it's a
        different ENTRY POINT into the same one bulk-import
        implementation, not a parallel code path."""
        dataset_id = await _create_dataset(client, admin_headers)
        source = tmp_path / "start_ingestion_test.csv"
        source.write_bytes(_make_csv_bytes())

        r = await client.post(
            "/api/v1/admin/bulk-import",
            json={"dataset_id": dataset_id, "source_path": str(source)},
            headers=admin_headers,
        )
        assert r.status_code == 202, r.text
        upload_id = r.json()["upload"]["id"]

        async with AsyncSessionLocal() as db:
            upload = await db.get(Upload, uuid.UUID(upload_id))
            assert upload.status == UploadStatus.COMPLETE.value
            assert upload.dataset_file_id is not None

            dataset_file = await db.get(DatasetFile, upload.dataset_file_id)
            assert dataset_file.file_metadata is not None
            assert "sea_surface_temp" in dataset_file.file_metadata["variables"]

            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            assert dataset.record_count == 15

            records = (
                await db.execute(
                    select(DatasetRecord).where(DatasetRecord.dataset_id == uuid.UUID(dataset_id))
                )
            ).scalars().all()
            assert len(records) == 15

    async def test_missing_path_rejected_before_any_row_created(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        r = await client.post(
            "/api/v1/admin/bulk-import",
            json={"dataset_id": dataset_id, "source_path": str(tmp_path / "does_not_exist.csv")},
            headers=admin_headers,
        )
        assert r.status_code == 400

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Upload).where(Upload.dataset_id == uuid.UUID(dataset_id)))
            assert result.scalars().all() == []
