"""Bulk-imports a file already present on a mounted server/NAS path directly
into MinIO and dispatches it through the exact same ingestion pipeline a
browser upload uses — for datasets already sitting on infrastructure the
operator controls (a NAS export, an existing archive on the VPS's own
disk) rather than something that needs to travel over HTTP at all.

This is explicitly Phase 2 territory, not Phase 1: it depends on
ingestion being chunked/RAM-safe (Phase 2's parser rewrites), since bulk
import exists specifically for 50GB-TB-scale files where the old
eager-loading parsers would OOM regardless of how the file arrived in
storage (see PLAN.md's Phase 2 §3 reasoning).

No parallel ingestion code path: this creates the same Upload/DatasetFile
shell rows a normal upload creates, then dispatches the same
process_dataset_file Celery task — bulk import is a different ENTRY
POINT into one ingestion pipeline, not a second pipeline.

Requires server/filesystem access to the source path — this is
necessarily a CLI script run where that mounted path is actually
reachable (on the VPS itself, or inside a backend container with the
NAS/archive volume-mounted in), not an HTTP endpoint an admin's browser
could reach a NAS through.

Usage:
    python -m app.scripts.bulk_import <dataset_id> <file_path> [--yes]

Without --yes, validates the file and prints what would happen (size,
detected extension, target storage key) without writing anything —
matching this project's other data-touching scripts' dry-run-by-default
convention.
"""

import argparse
import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetFile, StorageBackend
from app.models.uploads import Upload, UploadStatus
from app.services.ingestion_service import (
    UploadValidationError,
    compute_sha256,
    validate_content_matches_extension,
    validate_extension,
    validate_size,
)
from app.services.settings_service import get_settings
from app.services.storage import raw_key
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import ingestion_soft_time_limit_seconds
from app.worker.tasks.ingestion import process_dataset_file


class BulkImportError(Exception):
    pass


async def bulk_import(
    *,
    dataset_id: uuid.UUID,
    source_path: Path,
    storage_backend: str = StorageBackend.VPS_MINIO.value,
    dry_run: bool = True,
) -> dict:
    if not source_path.is_file():
        raise BulkImportError(f"No such file: {source_path}")

    async with AsyncSessionLocal() as db:
        dataset = await db.get(Dataset, dataset_id)
        if dataset is None:
            raise BulkImportError(f"No dataset found with id {dataset_id}")

        filename = source_path.name
        try:
            extension = validate_extension(filename)
        except UploadValidationError as exc:
            raise BulkImportError(str(exc)) from exc

        size_bytes = source_path.stat().st_size
        site_settings = await get_settings(db)
        try:
            validate_size(size_bytes, max_upload_size_mb=site_settings.max_upload_size_mb)
            validate_content_matches_extension(source_path, extension)
        except UploadValidationError as exc:
            raise BulkImportError(str(exc)) from exc

        file_id = uuid.uuid4()
        object_key = raw_key(dataset_id, file_id, filename)
        bucket = default_bucket_for(storage_backend)

        plan = {
            "dataset_id": str(dataset_id),
            "dataset_title": dataset.title,
            "source_path": str(source_path),
            "filename": filename,
            "size_bytes": size_bytes,
            "extension": extension,
            "target_bucket": bucket,
            "target_key": object_key,
        }

        if dry_run:
            plan["dry_run"] = True
            return plan

        # Same streaming discipline as the HTTP upload path
        # (dataset_file_service.upload_dataset_file) — storage.put()
        # internally uses boto3's upload_fileobj, which streams in chunks
        # (managed multipart transfer for large files) rather than
        # reading the whole object into memory; opening the source file
        # in binary mode here doesn't read it eagerly either, the file
        # object is handed straight to that streaming call.
        checksum = compute_sha256(source_path)
        storage = get_storage_backend(storage_backend)
        storage.ensure_bucket(bucket)

        with open(source_path, "rb") as f:
            stored = storage.put(
                bucket,
                object_key,
                f,
                content_type=_content_type_for(extension),
            )

        dataset_file = DatasetFile(
            id=file_id,
            dataset_id=dataset_id,
            file_name=filename,
            storage_backend=storage_backend,
            storage_bucket=bucket,
            storage_key=object_key,
            file_format=extension,
            file_size_bytes=stored.size_bytes,
            checksum=checksum,
            uploaded_by=None,  # bulk import is operator-run, not admin-attributed
        )
        db.add(dataset_file)

        upload = Upload(
            dataset_id=dataset_id,
            dataset_file_id=None,
            file_name=filename,
            size_bytes=stored.size_bytes,
            status=UploadStatus.QUEUED.value,
            uploaded_by=None,
        )
        db.add(upload)
        await db.commit()
        await db.refresh(dataset_file)
        await db.refresh(upload)

        task = process_dataset_file.apply_async(
            args=[str(dataset_file.id), str(upload.id)],
            soft_time_limit=ingestion_soft_time_limit_seconds(dataset_file.file_size_bytes),
        )
        upload.celery_task_id = task.id
        await db.commit()

        plan["dry_run"] = False
        plan["upload_id"] = str(upload.id)
        plan["dataset_file_id"] = str(dataset_file.id)
        plan["celery_task_id"] = task.id
        return plan


def _content_type_for(extension: str) -> str:
    return {
        "csv": "text/csv",
        "nc": "application/x-netcdf",
        "netcdf": "application/x-netcdf",
        "nc4": "application/x-netcdf",
        "mat": "application/octet-stream",
        "tif": "image/tiff",
        "tiff": "image/tiff",
        "geotiff": "image/tiff",
    }.get(extension, "application/octet-stream")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_id", type=str)
    parser.add_argument("file_path", type=str)
    parser.add_argument("--yes", action="store_true", help="Actually import (default is dry run)")
    args = parser.parse_args()

    result = asyncio.run(
        bulk_import(
            dataset_id=uuid.UUID(args.dataset_id),
            source_path=Path(args.file_path),
            dry_run=not args.yes,
        )
    )
    for key, value in result.items():
        print(f"{key}: {value}")
