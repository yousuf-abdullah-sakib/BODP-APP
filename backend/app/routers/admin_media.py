import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_media import MediaFilePublic, MediaFileUpdate
from app.services import admin_media_service
from app.services.admin_media_service import media_url

router = APIRouter(prefix="/admin/media", tags=["admin-media"])


def _to_public(media) -> MediaFilePublic:
    return MediaFilePublic(
        id=media.id,
        file_name=media.file_name,
        mime_type=media.mime_type,
        size_bytes=media.size_bytes,
        alt_text=media.alt_text,
        title=media.title,
        url=media_url(media),
        uploaded_at=media.uploaded_at,
    )


@router.get("", response_model=list[MediaFilePublic])
async def list_media(
    search: str | None = None,
    mime_prefix: str | None = None,
    current_user: User = Depends(require_permission("Manage Media")),
    db: AsyncSession = Depends(get_db),
):
    media = await admin_media_service.list_media(db, search=search, mime_prefix=mime_prefix)
    return [_to_public(m) for m in media]


@router.post("", response_model=MediaFilePublic, status_code=status.HTTP_201_CREATED)
async def upload_media(
    request: Request,
    file: UploadFile,
    alt_text: str | None = None,
    title: str | None = None,
    current_user: User = Depends(require_permission("Manage Media")),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    media = await admin_media_service.upload_media(
        db,
        filename=file.filename,
        file_stream=file,
        uploaded_by=current_user.id,
        alt_text=alt_text,
        title=title,
        actor=current_user,
        ip_address=get_client_ip(request),
    )
    return _to_public(media)


@router.patch("/{media_id}", response_model=MediaFilePublic)
async def update_media(
    media_id: uuid.UUID,
    payload: MediaFileUpdate,
    current_user: User = Depends(require_permission("Manage Media")),
    db: AsyncSession = Depends(get_db),
):
    media = await admin_media_service.get_media(db, media_id)
    media = await admin_media_service.update_media(
        db, media=media, alt_text=payload.alt_text, title=payload.title
    )
    return _to_public(media)


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(
    media_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Media")),
    db: AsyncSession = Depends(get_db),
):
    media = await admin_media_service.get_media(db, media_id)
    await admin_media_service.delete_media(
        db, media=media, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
