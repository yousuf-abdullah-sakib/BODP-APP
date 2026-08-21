import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_backups import BackupDownloadResponse, BackupPublic
from app.services import admin_backups_service

router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])


def _to_public(backup) -> BackupPublic:
    return BackupPublic(
        id=backup.id,
        started_at=backup.started_at,
        completed_at=backup.completed_at,
        size_bytes=backup.size_bytes,
        status=backup.status,
        celery_task_id=backup.celery_task_id,
        error_message=backup.error_message,
        ready=backup.storage_key is not None,
    )


@router.get("", response_model=list[BackupPublic])
async def list_backups(
    current_user: User = Depends(require_permission("Manage Backups")),
    db: AsyncSession = Depends(get_db),
):
    backups = await admin_backups_service.list_backups(db)
    return [_to_public(b) for b in backups]


@router.post("", response_model=BackupPublic, status_code=201)
async def create_backup(
    request: Request,
    current_user: User = Depends(require_permission("Manage Backups")),
    db: AsyncSession = Depends(get_db),
):
    backup = await admin_backups_service.create_backup(
        db, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_public(backup)


@router.get("/{backup_id}", response_model=BackupPublic)
async def get_backup(
    backup_id: uuid.UUID,
    current_user: User = Depends(require_permission("Manage Backups")),
    db: AsyncSession = Depends(get_db),
):
    backup = await admin_backups_service.get_backup(db, backup_id)
    return _to_public(backup)


@router.get("/{backup_id}/download", response_model=BackupDownloadResponse)
async def download_backup(
    backup_id: uuid.UUID,
    current_user: User = Depends(require_permission("Manage Backups")),
    db: AsyncSession = Depends(get_db),
):
    backup = await admin_backups_service.get_backup(db, backup_id)
    url = admin_backups_service.get_download_url(backup)
    return BackupDownloadResponse(download_url=url)
