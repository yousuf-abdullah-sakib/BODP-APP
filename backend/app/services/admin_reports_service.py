import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import Report
from app.models.audit import AuditActionType
from app.models.user import User
from app.schemas.admin_reports import ReportCreate
from app.services.audit_service import write_audit_log
from app.services.storage.registry import default_bucket_for, get_storage_backend

_REPORT_LABELS = {
    "usage_summary": "Usage Summary",
    "dataset_inventory": "Dataset Inventory",
    "user_activity": "User Activity",
    "access_grants": "Access Grants",
}


async def list_reports(db: AsyncSession) -> list[Report]:
    result = await db.execute(select(Report).order_by(Report.generated_at.desc()))
    return list(result.scalars().all())


async def get_report(db: AsyncSession, report_id: uuid.UUID) -> Report:
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


async def create_report(
    db: AsyncSession, *, payload: ReportCreate, actor: User, ip_address: str | None
) -> Report:
    from app.worker.tasks.reports import generate_report

    date_range = f"{payload.date_from} – {payload.date_to}" if payload.date_from and payload.date_to else "Full catalog"

    report = Report(type=payload.type, date_range=date_range, generated_by=actor.id)
    db.add(report)

    await write_audit_log(
        db,
        actor=actor,
        action=f"Generated {_REPORT_LABELS.get(payload.type, payload.type)} report",
        action_type=AuditActionType.CONTENT,
        target=date_range,
        ip_address=ip_address,
    )
    # Commit before dispatching so the row is visible to the task before it
    # runs. With Celery in eager mode (the test suite, and possible in any
    # environment where a worker picks up the task faster than expected)
    # .delay() executes generate_report synchronously in-process against a
    # separate sync DB session (get_sync_db) — if the report row were only
    # flushed and not committed here, that sync session would not see it
    # yet and the task would fail with "report not found".
    await db.commit()
    await db.refresh(report)

    task = generate_report.delay(str(report.id))
    report.celery_task_id = task.id
    await db.commit()
    await db.refresh(report)
    return report


def get_download_url(report: Report) -> str:
    if not report.output_storage_key:
        raise HTTPException(status_code=404, detail="Report is not ready yet")
    storage = get_storage_backend("vps_minio")
    bucket = default_bucket_for("vps_minio")
    return storage.presign_get(bucket, report.output_storage_key, expires_in_seconds=3600)
