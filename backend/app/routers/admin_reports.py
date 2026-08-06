import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_reports import ReportCreate, ReportDownloadResponse, ReportPublic
from app.services import admin_reports_service

router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])


def _to_public(report) -> ReportPublic:
    return ReportPublic(
        id=report.id,
        type=report.type,
        date_range=report.date_range,
        generated_by=report.generated_by,
        generated_at=report.generated_at,
        celery_task_id=report.celery_task_id,
        ready=report.output_storage_key is not None,
    )


@router.get("", response_model=list[ReportPublic])
async def list_reports(
    current_user: User = Depends(require_permission("View Reports")),
    db: AsyncSession = Depends(get_db),
):
    reports = await admin_reports_service.list_reports(db)
    return [_to_public(r) for r in reports]


@router.post("", response_model=ReportPublic, status_code=201)
async def create_report(
    payload: ReportCreate,
    request: Request,
    current_user: User = Depends(require_permission("View Reports")),
    db: AsyncSession = Depends(get_db),
):
    report = await admin_reports_service.create_report(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_public(report)


@router.get("/{report_id}", response_model=ReportPublic)
async def get_report(
    report_id: uuid.UUID,
    current_user: User = Depends(require_permission("View Reports")),
    db: AsyncSession = Depends(get_db),
):
    report = await admin_reports_service.get_report(db, report_id)
    return _to_public(report)


@router.get("/{report_id}/download", response_model=ReportDownloadResponse)
async def download_report(
    report_id: uuid.UUID,
    current_user: User = Depends(require_permission("View Reports")),
    db: AsyncSession = Depends(get_db),
):
    report = await admin_reports_service.get_report(db, report_id)
    url = admin_reports_service.get_download_url(report)
    return ReportDownloadResponse(download_url=url)
