from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.schemas.admin_general_settings import (
    GeneralSettingsSchema,
    GeneralSettingsUpdate,
    NotificationSettingsSchema,
    NotificationSettingsUpdate,
)
from app.schemas.admin_overview import StorageCapacitySchema, StorageCapacityUpdate
from app.schemas.visualize import (
    VizComputeLimitsSchema,
    VizComputeLimitsUpdate,
    VizExportSettingsSchema,
    VizExportSettingsUpdate,
)
from app.services import settings_service

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])


@router.get("/general", response_model=GeneralSettingsSchema)
async def get_general_settings(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await settings_service.get_settings(db)


@router.patch("/general", response_model=GeneralSettingsSchema)
async def update_general_settings(
    body: GeneralSettingsUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await settings_service.update_settings(db, body)


@router.get("/notifications", response_model=NotificationSettingsSchema)
async def get_notification_settings(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await settings_service.get_settings(db)


@router.patch("/notifications", response_model=NotificationSettingsSchema)
async def update_notification_settings(
    body: NotificationSettingsUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await settings_service.update_settings(db, body)


@router.get("/visualization-exports", response_model=VizExportSettingsSchema)
async def get_visualization_export_settings(db: AsyncSession = Depends(get_db)):
    """Public read — the /visualize page itself (also public, no auth) must
    be able to check these flags to decide whether to show export buttons,
    so this can't be admin-gated. Only PATCH requires admin."""
    return await settings_service.get_settings(db)


@router.patch("/visualization-exports", response_model=VizExportSettingsSchema)
async def update_visualization_export_settings(
    body: VizExportSettingsUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await settings_service.update_settings(db, body)


@router.get("/visualization-limits", response_model=VizComputeLimitsSchema)
async def get_visualization_compute_limits(db: AsyncSession = Depends(get_db)):
    """Public read — the public /visualize page needs these values to warn
    users before they submit an oversized request. Only PATCH requires
    admin."""
    return await settings_service.get_settings(db)


@router.patch("/visualization-limits", response_model=VizComputeLimitsSchema)
async def update_visualization_compute_limits(
    body: VizComputeLimitsUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await settings_service.update_settings(db, body)


@router.get("/storage-capacity", response_model=StorageCapacitySchema)
async def get_storage_capacity(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await settings_service.get_settings(db)


@router.patch("/storage-capacity", response_model=StorageCapacitySchema)
async def update_storage_capacity(
    body: StorageCapacityUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await settings_service.update_settings(db, body)
