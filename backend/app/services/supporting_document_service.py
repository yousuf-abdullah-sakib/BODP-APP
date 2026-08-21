import tempfile
import uuid
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.catalog import StorageBackend
from app.models.requests import RequestSupportingDocument
from app.services.storage.keys import supporting_document_key
from app.services.storage.registry import default_bucket_for, get_storage_backend

logger = structlog.get_logger(__name__)

_STREAM_CHUNK_SIZE = 1024 * 1024

_ALLOWED_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_ALLOWED_EXTENSIONS = tuple(_ALLOWED_CONTENT_TYPES)

# Magic-byte prefixes sufficient to distinguish these three document types
# from an arbitrary file that was merely renamed to a matching extension.
# DOC and DOCX share the general OLE2/ZIP container families used by many
# other formats, so this check rejects obvious mismatches (e.g. a renamed
# .exe or .png) without attempting a full structural parse — consistent
# with how validate_content_matches_extension() treats sniffing as a
# reject-the-obviously-wrong-file step, not a full-format validator.
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF-",),
    "doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),  # legacy OLE2 compound file
    "docx": (b"PK\x03\x04",),  # zip container
}


class SupportingDocumentUploadError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def validate_supporting_document_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in _ALLOWED_EXTENSIONS:
        raise SupportingDocumentUploadError(
            f"Unsupported file type '.{ext}'. Supported formats: "
            f"{', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )
    return ext


def validate_supporting_document_size(size_bytes: int) -> None:
    limit_mb = settings.MAX_SUPPORTING_DOCUMENT_SIZE_MB
    limit_bytes = limit_mb * 1024 * 1024
    if size_bytes > limit_bytes:
        raise SupportingDocumentUploadError(
            f"File is {size_bytes / (1024*1024):.1f}MB, which exceeds the "
            f"{limit_mb}MB supporting document upload limit"
        )
    if size_bytes == 0:
        raise SupportingDocumentUploadError("File is empty")


def _validate_content_matches_extension(path: Path, extension: str) -> None:
    signatures = _MAGIC_BYTES[extension]
    with open(path, "rb") as f:
        header = f.read(16)
    if not any(header.startswith(sig) for sig in signatures):
        raise SupportingDocumentUploadError(
            f"File content does not match its '.{extension}' extension"
        )


async def upload_supporting_document(
    db: AsyncSession,
    *,
    request_id: uuid.UUID,
    filename: str,
    file_stream,
    uploaded_by: uuid.UUID | None,
    storage_backend: str = StorageBackend.VPS_MINIO.value,
) -> RequestSupportingDocument:
    """Validates, stores, and links a Data Request supporting document.

    Storage happens BEFORE the DB row is created; if the DB commit fails
    the just-uploaded object is deleted so no orphaned storage object is
    left behind (delete() is idempotent, safe even if the object was
    somehow never created).
    """
    extension = validate_supporting_document_extension(filename)

    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}") as tmp:
        tmp_path = Path(tmp.name)
        total_bytes = 0
        while chunk := await file_stream.read(_STREAM_CHUNK_SIZE):
            tmp.write(chunk)
            total_bytes += len(chunk)

    object_key: str | None = None
    bucket: str | None = None
    storage = None
    try:
        validate_supporting_document_size(total_bytes)
        _validate_content_matches_extension(tmp_path, extension)

        document_id = uuid.uuid4()
        object_key = supporting_document_key(request_id, document_id, filename)
        bucket = default_bucket_for(storage_backend)
        storage = get_storage_backend(storage_backend)
        storage.ensure_bucket(bucket)

        with open(tmp_path, "rb") as f:
            stored = storage.put(
                bucket,
                object_key,
                f,
                content_type=_ALLOWED_CONTENT_TYPES[extension],
            )

        document = RequestSupportingDocument(
            id=document_id,
            request_id=request_id,
            uploaded_by=uploaded_by,
            storage_backend=storage_backend,
            storage_bucket=bucket,
            storage_key=object_key,
            original_filename=filename,
            content_type=_ALLOWED_CONTENT_TYPES[extension],
            file_size_bytes=stored.size_bytes,
        )
        db.add(document)

        try:
            await db.flush()
        except Exception:
            storage.delete(bucket, object_key)
            logger.warning(
                "supporting_document_db_flush_failed_cleaned_up_storage",
                request_id=str(request_id),
                key=object_key,
            )
            raise

        return document
    except SupportingDocumentUploadError:
        raise
    except Exception as exc:
        if storage is not None and bucket is not None and object_key is not None:
            storage.delete(bucket, object_key)
        raise SupportingDocumentUploadError(
            "Failed to store supporting document", status_code=500
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


async def get_supporting_document_for_request(
    db: AsyncSession, *, request_id: uuid.UUID, document_id: uuid.UUID
) -> RequestSupportingDocument | None:
    """Fetches a document only if it actually belongs to the given request
    — the authorization-relevant existence check the admin download
    endpoint needs to prevent arbitrary document-id access."""
    result = await db.execute(
        select(RequestSupportingDocument).where(
            RequestSupportingDocument.id == document_id,
            RequestSupportingDocument.request_id == request_id,
        )
    )
    return result.scalar_one_or_none()
