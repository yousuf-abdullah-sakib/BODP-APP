from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import SiteSettings
from app.schemas.visualize import VizComputeLimitsUpdate, VizExportSettingsUpdate

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


async def update_settings(
    db: AsyncSession, data: VizExportSettingsUpdate | VizComputeLimitsUpdate
) -> SiteSettings:
    settings = await get_settings(db)
    # exclude_unset (not exclude_none) — the compute-limit date-range
    # fields are meaningfully nullable ("unlimited"), so a PATCH body must
    # be able to explicitly set one back to null. exclude_unset applies
    # exactly the fields present in the request body, whether their value
    # is null or not, leaving omitted fields untouched either way.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)
    await db.commit()
    await db.refresh(settings)
    return settings
