import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.admin import Backup, BackupStatus
from app.models.audit import AuditActionType
from app.models.user import User
from app.services.audit_service import write_audit_log
from app.services.storage.registry import default_bucket_for, get_storage_backend


async def list_backups(db: AsyncSession) -> list[Backup]:
    result = await db.execute(select(Backup).order_by(Backup.started_at.desc()))
    return list(result.scalars().all())


async def get_backup(db: AsyncSession, backup_id: uuid.UUID) -> Backup:
    backup = await db.get(Backup, backup_id)
    if backup is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    return backup


async def create_backup(db: AsyncSession, *, actor: User, ip_address: str | None) -> Backup:
    from app.worker.tasks.backups import run_pg_dump

    backup = Backup(status=BackupStatus.RUNNING.value)
    db.add(backup)
    # Backup.id is a Python-side default (uuid.uuid4), not populated
    # until SQLAlchemy processes it at flush — flush (without a full
    # commit yet) makes it available for the audit log's target string
    # without a second round trip.
    await db.flush()

    await write_audit_log(
        db,
        actor=actor,
        action="Started an on-demand backup",
        action_type=AuditActionType.CONTENT,
        target=f"backup:{backup.id}",
        ip_address=ip_address,
    )
    # Commit before dispatching — same reasoning as admin_reports_service.
    # create_report: eager-mode Celery (test suite, or a fast worker) runs
    # run_pg_dump synchronously against a separate sync session that must
    # already see this row committed, not just flushed.
    await db.commit()
    await db.refresh(backup)

    task = run_pg_dump.delay(str(backup.id))
    backup.celery_task_id = task.id
    await db.commit()
    await db.refresh(backup)
    return backup


def get_download_url(backup: Backup) -> str:
    if not backup.storage_key:
        raise HTTPException(status_code=404, detail="Backup is not ready yet")
    storage = get_storage_backend("vps_minio")
    bucket = default_bucket_for("vps_minio")
    return storage.presign_get(
        bucket, backup.storage_key, expires_in_seconds=settings.BACKUP_DOWNLOAD_URL_EXPIRE_MINUTES * 60
    )
