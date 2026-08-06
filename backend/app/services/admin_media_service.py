import io
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.admin import AboutTeamMember, BlogPost, MediaFile
from app.models.audit import AuditActionType
from app.models.catalog import StorageBackend
from app.models.user import User
from app.services.audit_service import write_audit_log
from app.services.storage import media_key
from app.services.storage.registry import default_bucket_for, get_storage_backend

_MAX_MEDIA_SIZE_BYTES = 50 * 1024 * 1024
_STREAM_CHUNK_SIZE = 1024 * 1024
_ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "webp", "gif", "svg",
    "pdf", "csv", "xlsx", "doc", "docx",
}
_CONTENT_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif", "svg": "image/svg+xml",
    "pdf": "application/pdf", "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def media_url(media: MediaFile) -> str:
    base = settings.STORAGE_VPS_PUBLIC_ENDPOINT_URL or settings.STORAGE_VPS_ENDPOINT_URL
    return f"{base.rstrip('/')}/{media.storage_bucket}/{media.storage_key}"


async def list_media(
    db: AsyncSession, *, search: str | None = None, mime_prefix: str | None = None
) -> list[MediaFile]:
    query = select(MediaFile).order_by(MediaFile.uploaded_at.desc())
    if search:
        query = query.where(
            or_(MediaFile.file_name.ilike(f"%{search}%"), MediaFile.title.ilike(f"%{search}%"))
        )
    if mime_prefix:
        query = query.where(MediaFile.mime_type.ilike(f"{mime_prefix}%"))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_media(db: AsyncSession, media_id: uuid.UUID) -> MediaFile:
    media = await db.get(MediaFile, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media file not found")
    return media


async def upload_media(
    db: AsyncSession,
    *,
    filename: str,
    file_stream,
    uploaded_by: uuid.UUID,
    alt_text: str | None,
    title: str | None,
    actor: User,
    ip_address: str | None,
) -> MediaFile:
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{extension}'. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    chunks = []
    total_bytes = 0
    while chunk := await file_stream.read(_STREAM_CHUNK_SIZE):
        total_bytes += len(chunk)
        if total_bytes > _MAX_MEDIA_SIZE_BYTES:
            raise HTTPException(status_code=400, detail="File exceeds the 50MB size limit")
        chunks.append(chunk)
    if total_bytes == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    backend_name = StorageBackend.VPS_MINIO.value
    bucket = default_bucket_for(backend_name)
    storage = get_storage_backend(backend_name)
    storage.ensure_bucket(bucket)
    # Media renders as plain <img src>/<a href> on public pages, same
    # treatment as avatars — grant anonymous GET on the media/ prefix only.
    storage.set_public_prefix_policy(bucket, "media/")

    media = MediaFile(
        file_name=filename,
        storage_backend=backend_name,
        storage_bucket=bucket,
        storage_key="",  # set below once we have the row's id
        size_bytes=total_bytes,
        mime_type=_CONTENT_TYPES.get(extension),
        alt_text=alt_text,
        title=title,
        uploaded_by=uploaded_by,
    )
    db.add(media)
    await db.flush()  # assign media.id without committing yet

    key = media_key(media.id, filename)
    media.storage_key = key

    try:
        storage.put(bucket, key, io.BytesIO(b"".join(chunks)), content_type=_CONTENT_TYPES.get(extension))
    except Exception:
        await db.rollback()
        raise

    await write_audit_log(
        db,
        actor=actor,
        action="Uploaded media file",
        action_type=AuditActionType.CONTENT,
        target=media.file_name,
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(media)
    return media


async def update_media(
    db: AsyncSession, *, media: MediaFile, alt_text: str | None, title: str | None
) -> MediaFile:
    if alt_text is not None:
        media.alt_text = alt_text
    if title is not None:
        media.title = title
    await db.commit()
    await db.refresh(media)
    return media


async def delete_media(db: AsyncSession, *, media: MediaFile, actor: User, ip_address: str | None) -> None:
    blog_count = await db.scalar(
        select(func.count()).select_from(BlogPost).where(BlogPost.featured_image_id == media.id)
    )
    team_count = await db.scalar(
        select(func.count()).select_from(AboutTeamMember).where(AboutTeamMember.photo_id == media.id)
    )
    total_refs = (blog_count or 0) + (team_count or 0)
    if total_refs > 0:
        raise HTTPException(
            status_code=409,
            detail=f"File is still in use by {total_refs} item(s) — remove those references first.",
        )

    storage = get_storage_backend(media.storage_backend)
    storage.delete(media.storage_bucket, media.storage_key)

    await write_audit_log(
        db,
        actor=actor,
        action="Deleted media file",
        action_type=AuditActionType.CONTENT,
        target=media.file_name,
        ip_address=ip_address,
    )
    await db.delete(media)
    await db.commit()
