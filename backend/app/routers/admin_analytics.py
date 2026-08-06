from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_analytics import AnalyticsResponse
from app.services import admin_analytics_service

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"])


@router.get("", response_model=AnalyticsResponse)
async def get_analytics(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    current_user: User = Depends(require_permission("View Analytics")),
    db: AsyncSession = Depends(get_db),
):
    resolved_to = date_to or datetime.now(UTC)
    resolved_from = date_from or (resolved_to - timedelta(days=365))
    return await admin_analytics_service.get_analytics(db, date_from=resolved_from, date_to=resolved_to)
