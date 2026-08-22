from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.schemas.admin_health import DetailedHealthResponse
from app.services import admin_health_service

router = APIRouter(prefix="/admin/health", tags=["admin-health"])


@router.get("", response_model=DetailedHealthResponse)
async def detailed_health(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Read-only, low-risk — any admin can view (require_admin, not a
    specific permission like mutating actions), since this exposes no
    write capability, only operational visibility."""
    return await admin_health_service.get_detailed_health(db)
