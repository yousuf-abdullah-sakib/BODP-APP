import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import StorageBackend
from app.models.user import User
from app.schemas.me import ProfileUpdate
from app.services.storage import avatar_key
from app.services.storage.registry import default_bucket_for, get_storage_backend

_ALLOWED_AVATAR_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
_MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024
_AVATAR_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}
_STREAM_CHUNK_SIZE = 1024 * 1024


def update_profile(user: User, data: ProfileUpdate) -> User:
    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.institution is not None:
        user.institution = data.institution or None
    if data.phone is not None:
        user.phone = data.phone or None
    if data.bio is not None:
        user.bio = data.bio or None
    if data.research_area is not None:
        user.research_area = data.research_area or None
    return user


async def upload_avatar(db: AsyncSession, user: User, *, filename: str, file_stream) -> str:
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in _ALLOWED_AVATAR_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type '.{extension}'. Allowed: "
            f"{', '.join(sorted(_ALLOWED_AVATAR_EXTENSIONS))}",
        )

    chunks = []
    total_bytes = 0
    while chunk := await file_stream.read(_STREAM_CHUNK_SIZE):
        total_bytes += len(chunk)
        if total_bytes > _MAX_AVATAR_SIZE_BYTES:
            raise HTTPException(status_code=400, detail="Avatar image exceeds the 5MB size limit")
        chunks.append(chunk)
    if total_bytes == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    data = io.BytesIO(b"".join(chunks))

    backend_name = StorageBackend.VPS_MINIO.value
    bucket = default_bucket_for(backend_name)
    storage = get_storage_backend(backend_name)
    storage.ensure_bucket(bucket)
    # Avatars render as plain <img src> URLs (not presigned, unlike every
    # other object in this bucket) — grant anonymous GET on the avatars/
    # prefix only, leaving dataset files/extraction outputs private.
    storage.set_public_prefix_policy(bucket, "avatars/")

    key = avatar_key(user.id, f"{uuid.uuid4()}.{extension}")
    storage.put(bucket, key, data, content_type=_AVATAR_CONTENT_TYPES[extension])

    user.avatar_key = key
    await db.commit()
    return key


async def request_deletion(db: AsyncSession, user: User) -> datetime:
    if user.deletion_requested_at is None:
        user.deletion_requested_at = datetime.now(UTC)
        await db.commit()
    return user.deletion_requested_at


async def cancel_deletion(db: AsyncSession, user: User) -> None:
    user.deletion_requested_at = None
    await db.commit()
