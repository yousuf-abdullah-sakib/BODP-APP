import hashlib
from pathlib import Path

import structlog

from app.core.config import settings
from app.services.parsers import ParserError, sniff_format

logger = structlog.get_logger(__name__)

_SUPPORTED_EXTENSIONS = ("csv", "nc", "netcdf", "nc4", "mat", "tif", "tiff", "geotiff")

_CHECKSUM_CHUNK_SIZE = 1024 * 1024  # 1MB


class UploadValidationError(Exception):
    """Raised for any reason an uploaded file must be rejected before it's
    ever handed to a parser or written to storage — size, extension, or
    content-type mismatch (Master Plan §3 Phase 2 task 6)."""


def validate_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in _SUPPORTED_EXTENSIONS:
        raise UploadValidationError(
            f"Unsupported file type '.{ext}'. Supported formats: "
            f"{', '.join(sorted(set(_SUPPORTED_EXTENSIONS)))}"
        )
    return ext


def validate_size(size_bytes: int, *, max_upload_size_mb: int | None = None) -> None:
    limit_mb = max_upload_size_mb or settings.MAX_UPLOAD_SIZE_MB
    limit_bytes = limit_mb * 1024 * 1024
    if size_bytes > limit_bytes:
        raise UploadValidationError(
            f"File is {size_bytes / (1024*1024):.1f}MB, which exceeds the "
            f"{limit_mb}MB upload limit"
        )
    if size_bytes == 0:
        raise UploadValidationError("File is empty")


def validate_content_matches_extension(path: Path, extension: str) -> None:
    """Delegates to the parser registry's magic-byte sniff — kept as a
    distinct validation step (not folded into parsing itself) so a bad file
    is rejected before any parser attempts real work on it."""
    try:
        sniff_format(path, extension)
    except ParserError as exc:
        raise UploadValidationError(str(exc)) from exc


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHECKSUM_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(path: Path, expected_sha256: str) -> bool:
    return compute_sha256(path) == expected_sha256
