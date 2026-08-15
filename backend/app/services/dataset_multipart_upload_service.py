import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Dataset, DatasetFile, StorageBackend
from app.models.uploads import Upload, UploadStatus
from app.services.ingestion_service import UploadValidationError, validate_extension, validate_size
from app.services.storage import raw_key
from app.services.storage.registry import default_bucket_for, get_storage_backend

# S3-compatible multipart upload requires every part except the last to be
# at least 5MiB (a hard protocol minimum, not a tunable) — parts are sized
# well above that floor so a 50GB file doesn't need an unreasonable part
# count (50GB / 5MiB would be ~10,240 parts; S3's own hard ceiling is
# 10,000 parts per upload). 64MiB keeps even a 500GB file under that
# ceiling (~8,000 parts) with headroom for TB-scale files via a larger
# part size if ever needed — this constant is the one place that trade-off
# is made, not duplicated across call sites.
PART_SIZE_BYTES = 64 * 1024 * 1024

# Presigned part URLs need to outlive the time a slow client actually takes
# to PUT that one part, not the whole upload — each part is re-presignable
# individually if it expires, so this only needs to comfortably cover one
# 64MiB part on a slow connection, not the entire multi-hour transfer.
PART_URL_EXPIRES_SECONDS = 3600


class DatasetMultipartUploadError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _require_upload(upload: Upload | None) -> Upload:
    if upload is None:
        raise DatasetMultipartUploadError("Upload not found", status_code=404)
    return upload


async def initiate_multipart_upload(
    db: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    filename: str,
    total_size_bytes: int,
    uploaded_by: uuid.UUID | None,
    max_upload_size_mb: int | None = None,
    storage_backend: str = StorageBackend.VPS_MINIO.value,
) -> tuple[Upload, int]:
    """Starts a real S3 multipart upload session and returns (Upload row,
    total_parts) — the client uses total_parts to know how many
    presign-part calls to make and how to slice the file locally."""
    result = await db.execute(select(Dataset).where(Dataset.id == dataset_id))
    if result.scalar_one_or_none() is None:
        raise DatasetMultipartUploadError("Dataset not found", status_code=404)

    try:
        extension = validate_extension(filename)
        validate_size(total_size_bytes, max_upload_size_mb=max_upload_size_mb)
    except UploadValidationError as exc:
        raise DatasetMultipartUploadError(str(exc)) from exc

    file_id = uuid.uuid4()
    object_key = raw_key(dataset_id, file_id, filename)
    bucket = default_bucket_for(storage_backend)
    storage = get_storage_backend(storage_backend)
    storage.ensure_bucket(bucket)

    content_type = _content_type_for(extension)
    multipart_upload_id = storage.create_multipart_upload(bucket, object_key, content_type=content_type)

    total_parts = max(1, math.ceil(total_size_bytes / PART_SIZE_BYTES))

    upload = Upload(
        id=file_id,
        dataset_id=dataset_id,
        file_name=filename,
        size_bytes=total_size_bytes,
        status=UploadStatus.INITIATED.value,
        uploaded_by=uploaded_by,
        multipart_upload_id=multipart_upload_id,
        storage_bucket=bucket,
        storage_key=object_key,
        total_size_bytes=total_size_bytes,
        total_parts=total_parts,
        uploaded_bytes=0,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    return upload, total_parts


async def presign_part(
    db: AsyncSession, *, upload_id: uuid.UUID, part_number: int, storage_backend: str = StorageBackend.VPS_MINIO.value
) -> str:
    upload = _require_upload(await db.get(Upload, upload_id))
    if upload.status != UploadStatus.INITIATED.value:
        raise DatasetMultipartUploadError(
            f"Cannot presign a part for an upload in status '{upload.status}'"
        )
    if not (1 <= part_number <= (upload.total_parts or 0)):
        raise DatasetMultipartUploadError(
            f"part_number must be between 1 and {upload.total_parts}"
        )

    storage = get_storage_backend(storage_backend)
    return storage.presign_upload_part(
        upload.storage_bucket,
        upload.storage_key,
        upload_id=upload.multipart_upload_id,
        part_number=part_number,
        expires_in_seconds=PART_URL_EXPIRES_SECONDS,
    )


async def mark_part_uploaded(
    db: AsyncSession, *, upload_id: uuid.UUID, part_number: int, size_bytes: int
) -> Upload:
    """Called by the client after each part's direct PUT to storage
    succeeds — this is what makes upload-progress real (driven by
    confirmed part completions), not a client-side estimate the server
    just trusts blindly."""
    upload = _require_upload(await db.get(Upload, upload_id))
    if upload.status != UploadStatus.INITIATED.value:
        raise DatasetMultipartUploadError(
            f"Cannot record part progress for an upload in status '{upload.status}'"
        )
    # Idempotent-ish accumulation: a client retry of the same part_number
    # (e.g. after a flaky network response to the PUT, even though the PUT
    # itself succeeded) would double-count bytes here. The authoritative
    # progress figure at completion time is reconciled against storage's
    # own list_parts() in complete_multipart_upload below, so this field is
    # a live-progress *estimate* for the UI, not the source of truth used
    # to finalize the upload.
    upload.uploaded_bytes = min((upload.uploaded_bytes or 0) + size_bytes, upload.total_size_bytes or size_bytes)
    await db.commit()
    await db.refresh(upload)
    return upload


async def complete_multipart_upload(
    db: AsyncSession, *, upload_id: uuid.UUID, storage_backend: str = StorageBackend.VPS_MINIO.value
) -> tuple[Upload, DatasetFile]:
    upload = _require_upload(await db.get(Upload, upload_id))
    if upload.status != UploadStatus.INITIATED.value:
        raise DatasetMultipartUploadError(
            f"Cannot complete an upload in status '{upload.status}'"
        )

    storage = get_storage_backend(storage_backend)

    # Reconcile against storage's own record of what actually arrived,
    # rather than trusting the client's mark_part_uploaded() calls — a part
    # the client claims to have PUT but that storage never received must
    # not be allowed to finalize.
    real_parts = storage.list_parts(
        upload.storage_bucket, upload.storage_key, upload_id=upload.multipart_upload_id
    )
    if len(real_parts) != upload.total_parts:
        raise DatasetMultipartUploadError(
            f"Expected {upload.total_parts} parts, storage has {len(real_parts)} — "
            "upload is incomplete."
        )

    parts_payload = [
        {"PartNumber": p["PartNumber"], "ETag": p["ETag"]}
        for p in sorted(real_parts, key=lambda p: p["PartNumber"])
    ]
    stored = storage.complete_multipart_upload(
        upload.storage_bucket,
        upload.storage_key,
        upload_id=upload.multipart_upload_id,
        parts=parts_payload,
    )

    extension = (upload.file_name.rsplit(".", 1)[-1] if "." in upload.file_name else "").lower()
    dataset_file = DatasetFile(
        id=upload.id,
        dataset_id=upload.dataset_id,
        file_name=upload.file_name,
        storage_backend=storage_backend,
        storage_bucket=upload.storage_bucket,
        storage_key=upload.storage_key,
        file_format=extension,
        file_size_bytes=stored.size_bytes,
        uploaded_by=upload.uploaded_by,
    )
    db.add(dataset_file)

    upload.status = UploadStatus.QUEUED.value
    upload.uploaded_bytes = upload.total_size_bytes or upload.uploaded_bytes
    upload.dataset_file_id = dataset_file.id

    await db.commit()
    await db.refresh(upload)
    await db.refresh(dataset_file)

    return upload, dataset_file


async def cancel_upload(
    db: AsyncSession, *, upload_id: uuid.UUID, storage_backend: str = StorageBackend.VPS_MINIO.value
) -> Upload:
    """Cancellation is handled differently depending on how far the upload
    got, per the Phase 1 plan's cancellation requirements — a cancelled
    upload must never remain/transition to 'processing', must never leave
    an orphaned DatasetFile/Dataset shell row, and any partial MinIO state
    (multipart session or a fully-uploaded-but-not-yet-ingested object)
    must be cleaned up:

    - INITIATED: file bytes are still mid-transfer (a multipart session
      exists, no DatasetFile yet). Abort the multipart session directly —
      storage discards whatever parts already arrived.
    - QUEUED: the file finished uploading (a real DatasetFile row and a
      real object exist in storage) but ingestion hasn't started yet — no
      Celery task to stop, so this deletes the DatasetFile row and its raw
      object immediately, synchronously, right here.
    - PROCESSING: an ingestion Celery task is (or recently was) running.
      There is no safe way to kill a task mid-library-call (e.g. inside a
      blocking xr.open_dataset() read) without risking a corrupt partial
      write, so this does NOT delete anything itself — it best-effort
      revokes the task (stops it before its next checkpoint if it hasn't
      reached one yet) and flips status to CANCELLED immediately so the
      UI never shows 'processing' again. process_dataset_file's own
      checkpoints (ingestion.py) detect CANCELLED and perform the actual
      DatasetFile/processed-artifact/DatasetRecord cleanup from inside the
      task itself, once it's safely between library calls — this mirrors
      why FAILED cleanup already lives in the task, not the cancel caller.
    """
    upload = _require_upload(await db.get(Upload, upload_id))
    storage = get_storage_backend(storage_backend)

    if upload.status == UploadStatus.INITIATED.value:
        if upload.multipart_upload_id:
            storage.abort_multipart_upload(
                upload.storage_bucket, upload.storage_key, upload_id=upload.multipart_upload_id
            )
        upload.status = UploadStatus.CANCELLED.value
        upload.error_message = "Cancelled before upload completed."

    elif upload.status == UploadStatus.QUEUED.value:
        if upload.dataset_file_id:
            dataset_file = await db.get(DatasetFile, upload.dataset_file_id)
            if dataset_file is not None:
                storage.delete(dataset_file.storage_bucket, dataset_file.storage_key)
                await db.delete(dataset_file)
        elif upload.storage_bucket and upload.storage_key:
            storage.delete(upload.storage_bucket, upload.storage_key)
        upload.dataset_file_id = None
        upload.status = UploadStatus.CANCELLED.value
        upload.error_message = "Cancelled after upload completed, before processing started."

    elif upload.status == UploadStatus.PROCESSING.value:
        if upload.celery_task_id:
            from app.worker.celery_app import celery_app

            celery_app.control.revoke(upload.celery_task_id, terminate=True)
        upload.status = UploadStatus.CANCELLED.value
        upload.error_message = "Cancellation requested during processing."

    else:
        raise DatasetMultipartUploadError(
            f"Cannot cancel an upload already in terminal status '{upload.status}'"
        )

    await db.commit()
    await db.refresh(upload)
    return upload


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
