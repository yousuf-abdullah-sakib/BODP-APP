from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_audit import AuditLogPage
from app.services import admin_audit_service

router = APIRouter(prefix="/admin/audit-log", tags=["admin-audit"])


@router.get("", response_model=AuditLogPage)
async def list_audit_log(
    search: str | None = None,
    action_type: str | None = None,
    page: int = 1,
    page_size: int = 8,
    current_user: User = Depends(require_permission("View Audit Log")),
    db: AsyncSession = Depends(get_db),
):
    items, total = await admin_audit_service.list_entries(
        db, search=search, action_type=action_type, page=page, page_size=page_size
    )
    return AuditLogPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/export")
async def export_audit_log(
    search: str | None = None,
    action_type: str | None = None,
    current_user: User = Depends(require_permission("View Audit Log")),
    db: AsyncSession = Depends(get_db),
):
    csv_text = await admin_audit_service.export_csv(db, search=search, action_type=action_type)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bodp_audit_log.csv"},
    )
