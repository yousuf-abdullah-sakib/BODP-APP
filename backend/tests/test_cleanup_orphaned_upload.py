import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetFile, StorageBackend
from app.models.uploads import Upload, UploadStatus
from app.scripts.cleanup_orphaned_upload import cleanup_orphaned_upload

pytestmark = pytest.mark.asyncio


class _FakeStorage:
    def __init__(self):
        self.deleted_calls = []

    def delete(self, bucket, key):
        self.deleted_calls.append((bucket, key))


async def _seed_orphaned_upload(*, linked: bool, with_processed_artifact: bool = False):
    """Simulates the exact BoB_WaveData_2010_2024.nc scenario: an Upload
    stuck in 'processing' with a raw DatasetFile that never got
    file_metadata (ingestion never completed) and, per the real case, was
    never linked back via dataset_file_id (only set on completion)."""
    async with AsyncSessionLocal() as db:
        dataset = Dataset(code=f"CLEANUP-{uuid.uuid4().hex[:8]}", title="Cleanup Test Dataset")
        db.add(dataset)
        await db.flush()

        dataset_file = DatasetFile(
            dataset_id=dataset.id,
            file_name="orphaned.nc",
            storage_backend=StorageBackend.VPS_MINIO.value,
            storage_bucket="bodp-vps",
            storage_key=f"raw/{dataset.id}/orphaned.nc",
            file_metadata=(
                {"processed_key": f"processed/{dataset.id}/orphaned.parquet", "processed_bucket": "bodp-vps"}
                if with_processed_artifact
                else None
            ),
        )
        db.add(dataset_file)
        await db.flush()

        upload = Upload(
            dataset_id=dataset.id,
            dataset_file_id=dataset_file.id if linked else None,
            file_name="orphaned.nc",
            status=UploadStatus.PROCESSING.value,
            celery_task_id="dead-task-id",
        )
        db.add(upload)
        await db.commit()
        return upload.id, dataset_file.id, dataset.id


class TestCleanupOrphanedUpload:
    async def test_dry_run_changes_nothing(self, monkeypatch):
        upload_id, dataset_file_id, _ = await _seed_orphaned_upload(linked=False)

        fake_storage = _FakeStorage()
        from app.scripts import cleanup_orphaned_upload as module

        monkeypatch.setattr(module, "get_storage_backend", lambda backend: fake_storage)

        plan = await cleanup_orphaned_upload(upload_id, dry_run=True)
        assert plan["dry_run"] is True
        assert plan["dataset_file_id"] == str(dataset_file_id)
        assert fake_storage.deleted_calls == []

        async with AsyncSessionLocal() as db:
            upload = await db.get(Upload, upload_id)
            assert upload.status == UploadStatus.PROCESSING.value
            assert await db.get(DatasetFile, dataset_file_id) is not None

    async def test_cleanup_marks_failed_deletes_file_row_and_storage_object(self, monkeypatch):
        upload_id, dataset_file_id, _ = await _seed_orphaned_upload(linked=False)

        fake_storage = _FakeStorage()
        from app.scripts import cleanup_orphaned_upload as module

        monkeypatch.setattr(module, "get_storage_backend", lambda backend: fake_storage)

        result = await cleanup_orphaned_upload(upload_id, dry_run=False)
        assert result["upload_status_after"] == UploadStatus.FAILED.value

        async with AsyncSessionLocal() as db:
            upload = await db.get(Upload, upload_id)
            assert upload.status == UploadStatus.FAILED.value
            assert upload.dataset_file_id is None
            assert await db.get(DatasetFile, dataset_file_id) is None

        assert len(fake_storage.deleted_calls) == 1
        assert fake_storage.deleted_calls[0][0] == "bodp-vps"

    async def test_cleanup_also_removes_processed_artifact_if_present(self, monkeypatch):
        upload_id, dataset_file_id, _ = await _seed_orphaned_upload(
            linked=True, with_processed_artifact=True
        )

        fake_storage = _FakeStorage()
        from app.scripts import cleanup_orphaned_upload as module

        monkeypatch.setattr(module, "get_storage_backend", lambda backend: fake_storage)

        await cleanup_orphaned_upload(upload_id, dry_run=False)

        # Both the raw object and the processed artifact should be deleted.
        assert len(fake_storage.deleted_calls) == 2
        deleted_buckets = {call[0] for call in fake_storage.deleted_calls}
        assert deleted_buckets == {"bodp-vps"}

    async def test_unlinked_upload_resolves_dataset_file_by_filename(self):
        """The real BoB_WaveData_2010_2024.nc case: dataset_file_id was
        never set on the Upload because ingestion never reached the
        completion step that sets it — cleanup must still find the
        DatasetFile via dataset_id + file_name."""
        upload_id, dataset_file_id, _ = await _seed_orphaned_upload(linked=False)

        plan = await cleanup_orphaned_upload(upload_id, dry_run=True)
        assert plan["dataset_file_id"] == str(dataset_file_id)

    async def test_raises_for_unknown_upload(self):
        with pytest.raises(ValueError):
            await cleanup_orphaned_upload(uuid.uuid4(), dry_run=True)
