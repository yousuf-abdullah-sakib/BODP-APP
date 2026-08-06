from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.schemas.admin_overview import AdminOverviewResponse
from app.services import admin_overview_service

router = APIRouter(prefix="/admin", tags=["admin-overview"])


@router.get("/overview", response_model=AdminOverviewResponse)
async def get_overview(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await admin_overview_service.get_admin_overview(db)
