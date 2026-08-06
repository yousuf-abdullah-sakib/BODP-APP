from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get_json, cache_set_json
from app.models.admin import SiteSettings
from app.schemas.admin_general_settings import GeneralSettingsUpdate, NotificationSettingsUpdate
from app.schemas.admin_overview import StorageCapacityUpdate
from app.schemas.visualize import VizComputeLimitsUpdate, VizExportSettingsUpdate

# SiteSettings is a singleton row (id=1, per the model's own default) with
# no provisioning step anywhere in the app yet — this is the first real
# accessor for it.

# Cache key for session_lifetime_min — read by app.core.security's
# token-issuing path on every login/refresh (hot path), so it's cached
# rather than hitting the DB per request, invalidated on every settings
# write below (Phase 9 general-settings PATCH).
SESSION_LIFETIME_CACHE_KEY = "site_settings:session_lifetime_min"


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
    db: AsyncSession,
    data: VizExportSettingsUpdate
    | VizComputeLimitsUpdate
    | StorageCapacityUpdate
    | GeneralSettingsUpdate
    | NotificationSettingsUpdate,
) -> SiteSettings:
    settings = await get_settings(db)
    # exclude_unset (not exclude_none) — the compute-limit date-range
    # fields are meaningfully nullable ("unlimited"), so a PATCH body must
    # be able to explicitly set one back to null. exclude_unset applies
    # exactly the fields present in the request body, whether their value
    # is null or not, leaving omitted fields untouched either way.
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(settings, field, value)
    await db.commit()
    await db.refresh(settings)

    if "session_lifetime_min" in updates:
        await cache_delete(SESSION_LIFETIME_CACHE_KEY)

    return settings


async def get_session_lifetime_minutes(db: AsyncSession) -> int:
    """Resolves the admin-configured token lifetime for create_access_token
    (see app/core/security.py) — Redis-cached since this sits on the login/
    refresh hot path, invalidated by update_settings above whenever an
    admin changes it. Falls back to the real DB value on a cache-read
    failure; never blocks login on a Redis outage."""
    cached = await cache_get_json(SESSION_LIFETIME_CACHE_KEY)
    if cached is not None:
        return cached

    settings = await get_settings(db)
    await cache_set_json(SESSION_LIFETIME_CACHE_KEY, settings.session_lifetime_min, ttl_seconds=3600)
    return settings.session_lifetime_min
