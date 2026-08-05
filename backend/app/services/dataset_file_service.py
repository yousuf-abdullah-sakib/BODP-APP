import tempfile
import uuid
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Dataset, DatasetFile, StorageBackend
from app.models.uploads import Upload, UploadStatus
from app.services.ingestion_service import (
    UploadValidationError,
    compute_sha256,
    validate_content_matches_extension,
    validate_extension,
    validate_size,
)
from app.services.storage import raw_key
from app.services.storage.registry import default_bucket_for, get_storage_backend

logger = structlog.get_logger(__name__)

# Streamed to a temp file in chunks rather than read()'d whole, so a large
# NetCDF/CSV upload never sits fully in worker memory (Master Plan §3 Phase 2
# quality check: "large file upload doesn't block the request thread").
_STREAM_CHUNK_SIZE = 1024 * 1024


class DatasetFileUploadError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def upload_dataset_file(
    db: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    filename: str,
    file_stream,
    uploaded_by: uuid.UUID | None,
    max_upload_size_mb: int | None = None,
    storage_backend: str = StorageBackend.VPS_MINIO.value,
) -> tuple[Upload, DatasetFile]:
    result = await db.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise DatasetFileUploadError("Dataset not found", status_code=404)

    try:
        extension = validate_extension(filename)
    except UploadValidationError as exc:
        raise DatasetFileUploadError(str(exc)) from exc

    # Stream the upload to a local temp file first — this lets us validate
    # size/content and compute a checksum before committing anything to
    # object storage or the database, and avoids holding the whole file in
    # memory regardless of how large it is.
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}") as tmp:
        tmp_path = Path(tmp.name)
        total_bytes = 0
        while chunk := await file_stream.read(_STREAM_CHUNK_SIZE):
            tmp.write(chunk)
            total_bytes += len(chunk)

    try:
        try:
            validate_size(total_bytes, max_upload_size_mb=max_upload_size_mb)
            validate_content_matches_extension(tmp_path, extension)
        except UploadValidationError as exc:
            raise DatasetFileUploadError(str(exc)) from exc

        checksum = compute_sha256(tmp_path)

        file_id = uuid.uuid4()
        object_key = raw_key(dataset_id, file_id, filename)
        bucket = default_bucket_for(storage_backend)
        storage = get_storage_backend(storage_backend)
        storage.ensure_bucket(bucket)

        with open(tmp_path, "rb") as f:
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
            uploaded_by=uploaded_by,
        )
        db.add(dataset_file)

        upload = Upload(
            dataset_id=dataset_id,
            dataset_file_id=None,
            file_name=filename,
            size_bytes=stored.size_bytes,
            status=UploadStatus.QUEUED.value,
            uploaded_by=uploaded_by,
        )
        db.add(upload)

        await db.commit()
        await db.refresh(dataset_file)
        await db.refresh(upload)

        return upload, dataset_file
    finally:
        tmp_path.unlink(missing_ok=True)


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
