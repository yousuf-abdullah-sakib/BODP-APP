import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserPreferences
from app.schemas.me import PreferencesSchema


async def get_preferences(db: AsyncSession, user_id: uuid.UUID) -> UserPreferences:
    result = await db.execute(select(UserPreferences).where(UserPreferences.user_id == user_id))
    prefs = result.scalar_one_or_none()
    if prefs is None:
        prefs = UserPreferences(user_id=user_id)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return prefs


async def update_preferences(
    db: AsyncSession, user_id: uuid.UUID, data: PreferencesSchema
) -> UserPreferences:
    prefs = await get_preferences(db, user_id)
    for field, value in data.model_dump().items():
        setattr(prefs, field, value)
    await db.commit()
    await db.refresh(prefs)
    return prefs
