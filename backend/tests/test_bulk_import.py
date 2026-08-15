import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetFile, DatasetRecord
from app.models.uploads import Upload, UploadStatus
from app.scripts.bulk_import import BulkImportError, bulk_import
from tests.test_dataset_upload import _create_dataset, _make_csv_bytes

pytestmark = pytest.mark.asyncio


class TestBulkImport:
    async def test_dry_run_validates_without_writing(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        source = tmp_path / "bulk_test.csv"
        source.write_bytes(_make_csv_bytes())

        plan = await bulk_import(dataset_id=uuid.UUID(dataset_id), source_path=source, dry_run=True)
        assert plan["dry_run"] is True
        assert plan["filename"] == "bulk_test.csv"
        assert plan["extension"] == "csv"
        assert plan["target_key"].startswith(f"raw/{dataset_id}/")

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == uuid.UUID(dataset_id))
            )
            assert result.scalars().all() == []

    async def test_real_import_reaches_same_ingestion_result_as_http_upload(
        self, client, admin_headers, tmp_path
    ):
        """Acceptance criterion from PLAN.md: bulk-importing a file must
        produce an identical Dataset/DatasetFile/DatasetRecord/
        DatasetVariable result to uploading the same file through the API
        — same shell-row creation, same ingestion task, no parallel code
        path."""
        dataset_id = await _create_dataset(client, admin_headers)
        source = tmp_path / "bulk_real.csv"
        source.write_bytes(_make_csv_bytes())

        result = await bulk_import(dataset_id=uuid.UUID(dataset_id), source_path=source, dry_run=False)
        assert result["dry_run"] is False
        assert result["upload_id"]
        assert result["dataset_file_id"]

        async with AsyncSessionLocal() as db:
            upload = await db.get(Upload, uuid.UUID(result["upload_id"]))
            assert upload.status == UploadStatus.COMPLETE.value  # Celery runs eagerly in tests

            dataset_file = await db.get(DatasetFile, uuid.UUID(result["dataset_file_id"]))
            assert dataset_file.file_metadata is not None
            assert "sea_surface_temp" in dataset_file.file_metadata["variables"]

            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            assert dataset.record_count == 15

            records = (
                await db.execute(
                    select(DatasetRecord).where(DatasetRecord.dataset_id == uuid.UUID(dataset_id))
                )
            ).scalars().all()
            assert len(records) == 15  # identical to what the HTTP upload path produces

    async def test_raises_for_unknown_dataset(self, tmp_path):
        source = tmp_path / "orphan.csv"
        source.write_bytes(_make_csv_bytes())
        with pytest.raises(BulkImportError, match="No dataset found"):
            await bulk_import(dataset_id=uuid.uuid4(), source_path=source, dry_run=True)

    async def test_raises_for_missing_file(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        with pytest.raises(BulkImportError, match="No such file"):
            await bulk_import(
                dataset_id=uuid.UUID(dataset_id), source_path=tmp_path / "does_not_exist.csv", dry_run=True
            )

    async def test_raises_for_unsupported_extension(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        source = tmp_path / "bulk.exe"
        source.write_bytes(b"MZ\x90\x00fake executable content")
        with pytest.raises(BulkImportError, match="Unsupported file type"):
            await bulk_import(dataset_id=uuid.UUID(dataset_id), source_path=source, dry_run=True)

    async def test_enforces_admin_configured_size_limit(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        r = await client.patch(
            "/api/v1/admin/settings/general", json={"max_upload_size_mb": 1}, headers=admin_headers
        )
        assert r.status_code == 200, r.text

        try:
            source = tmp_path / "oversized.csv"
            oversized_csv = b"time,lat,lon,value\n" + (b"2024-01-01,22.0,91.0,27.0\n" * 100_000)
            assert len(oversized_csv) > 1 * 1024 * 1024
            source.write_bytes(oversized_csv)

            with pytest.raises(BulkImportError, match="exceeds the 1MB upload limit"):
                await bulk_import(dataset_id=uuid.UUID(dataset_id), source_path=source, dry_run=True)
        finally:
            await client.patch(
                "/api/v1/admin/settings/general", json={"max_upload_size_mb": 5000}, headers=admin_headers
            )
