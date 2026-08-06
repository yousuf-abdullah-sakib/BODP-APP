import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_qc import QcScanResult, QualityIssuePublic, QualityIssueStatusUpdate
from app.services import admin_qc_service

router = APIRouter(prefix="/admin/qc", tags=["admin-qc"])


def _to_public(issue) -> QualityIssuePublic:
    return QualityIssuePublic(
        id=issue.id,
        dataset_id=issue.dataset_id,
        dataset_title=issue.dataset.title if issue.dataset else "",
        issue_type=issue.issue_type,
        severity=issue.severity,
        status=issue.status,
        detail=issue.detail,
        detected_at=issue.detected_at,
    )


@router.get("/issues", response_model=list[QualityIssuePublic])
async def list_issues(
    status_filter: str | None = None,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    issues = await admin_qc_service.list_quality_issues(db, status_filter=status_filter)
    return [_to_public(i) for i in issues]


@router.post("/scan", response_model=QcScanResult)
async def run_scan(
    request: Request,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    new_issues = await admin_qc_service.run_qc_scan(
        db, actor=current_user, ip_address=get_client_ip(request)
    )
    issues = await admin_qc_service.list_quality_issues(db, status_filter=None)
    new_ids = {i.id for i in new_issues}
    return QcScanResult(
        issues_found=len(new_issues),
        issues=[_to_public(i) for i in issues if i.id in new_ids],
    )


@router.patch("/issues/{issue_id}", response_model=QualityIssuePublic)
async def update_issue_status(
    issue_id: uuid.UUID,
    payload: QualityIssueStatusUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    issue = await admin_qc_service.get_quality_issue(db, issue_id)
    issue = await admin_qc_service.update_issue_status(
        db, issue=issue, status=payload.status, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_public(issue)
