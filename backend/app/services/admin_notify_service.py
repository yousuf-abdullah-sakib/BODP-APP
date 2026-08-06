from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications import Notification
from app.models.user import User, UserRoleEnum


async def notify_admins(db: AsyncSession, *, type: str, title: str, description: str) -> None:
    """Inserts one Notification row per admin user. Caller is responsible
    for committing (matches this codebase's convention of committing once
    per logical operation, not per side-effect)."""
    result = await db.execute(select(User.id).where(User.role == UserRoleEnum.ADMIN.value))
    admin_ids = result.scalars().all()
    for admin_id in admin_ids:
        db.add(Notification(user_id=admin_id, type=type, title=title, description=description))
