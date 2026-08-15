"""Cleans up an orphaned upload — one that was cancelled/abandoned before
ingestion ever completed, left stuck in `processing` with an incomplete
`DatasetFile` row (no `file_metadata`) and no live Celery task actually
running it.

This is NOT a general "retry failed uploads" tool and does not attempt to
re-run ingestion — a stuck upload's raw file may be truncated, its Celery
task may have died mid-write, and blindly re-ingesting an abandoned upload
risks treating incomplete data as valid. The correct fix is to remove the
orphaned state entirely so the admin can re-upload a fresh copy if needed.

Usage:
    python -m app.scripts.cleanup_orphaned_upload <upload_id> [--yes]

Without --yes, prints what would be deleted and exits without changing
anything (dry run).
"""

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import DatasetFile
from app.models.uploads import Upload, UploadStatus
from app.services.storage.registry import get_storage_backend


async def cleanup_orphaned_upload(upload_id: uuid.UUID, *, dry_run: bool = True) -> dict:
    async with AsyncSessionLocal() as db:
        upload = await db.get(Upload, upload_id)
        if upload is None:
            raise ValueError(f"No upload found with id {upload_id}")

        dataset_file = None
        if upload.dataset_file_id is not None:
            dataset_file = await db.get(DatasetFile, upload.dataset_file_id)
        else:
            # The upload never got far enough to link its DatasetFile back
            # to itself (process_dataset_file only sets dataset_file_id on
            # successful completion) — fall back to matching by filename
            # under the same dataset, which is how the raw file was
            # actually created (dataset_file_service.upload_dataset_file
            # writes the DatasetFile row before dispatching ingestion).
            if upload.dataset_id is not None:
                result = await db.execute(
                    select(DatasetFile).where(
                        DatasetFile.dataset_id == upload.dataset_id,
                        DatasetFile.file_name == upload.file_name,
                    )
                )
                dataset_file = result.scalar_one_or_none()

        plan = {
            "upload_id": str(upload.id),
            "upload_status_before": upload.status,
            "dataset_file_id": str(dataset_file.id) if dataset_file else None,
            "storage_key": dataset_file.storage_key if dataset_file else None,
            "storage_bucket": dataset_file.storage_bucket if dataset_file else None,
            "has_processed_artifact": bool(dataset_file and dataset_file.file_metadata),
        }

        if dry_run:
            plan["dry_run"] = True
            return plan

        if dataset_file is not None:
            if dataset_file.file_metadata:
                # Ingestion had already produced a processed artifact before
                # the upload was cancelled/abandoned — clean that up too so
                # nothing is left behind in processed/.
                storage = get_storage_backend(dataset_file.storage_backend)
                processed_key = dataset_file.file_metadata.get("processed_key")
                processed_bucket = dataset_file.file_metadata.get("processed_bucket")
                if processed_key and processed_bucket:
                    storage.delete(processed_bucket, processed_key)

            storage = get_storage_backend(dataset_file.storage_backend)
            storage.delete(dataset_file.storage_bucket, dataset_file.storage_key)
            await db.delete(dataset_file)

        upload.status = UploadStatus.FAILED.value
        upload.error_message = (
            "Upload was cancelled/abandoned before processing completed. "
            "Cleaned up by cleanup_orphaned_upload — re-upload the file to try again."
        )
        upload.dataset_file_id = None

        await db.commit()

        plan["dry_run"] = False
        plan["upload_status_after"] = upload.status
        return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("upload_id", type=str)
    parser.add_argument("--yes", action="store_true", help="Actually perform the cleanup (default is dry run)")
    args = parser.parse_args()

    result = asyncio.run(
        cleanup_orphaned_upload(uuid.UUID(args.upload_id), dry_run=not args.yes)
    )
    for key, value in result.items():
        print(f"{key}: {value}")
