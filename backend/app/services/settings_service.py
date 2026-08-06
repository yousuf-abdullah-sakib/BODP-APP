from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import SiteSettings
from app.schemas.visualize import VizExportSettingsUpdate

# SiteSettings is a singleton row (id=1, per the model's own default) with
# no provisioning step anywhere in the app yet — this is the first real
# accessor for it. Deliberately minimal: Phase 9 extends this file with
# the rest of the Settings admin screen rather than replacing it.


async def get_settings(db: AsyncSession) -> SiteSettings:
    result = await db.execute(select(SiteSettings).where(SiteSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = SiteSettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def update_settings(db: AsyncSession, data: VizExportSettingsUpdate) -> SiteSettings:
    settings = await get_settings(db)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(settings, field, value)
    await db.commit()
    await db.refresh(settings)
    return settings
