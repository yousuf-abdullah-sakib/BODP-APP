from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications import Notification
from app.models.user import Role, User, UserRole, UserRoleEnum


async def notify_admins(db: AsyncSession, *, type: str, title: str, description: str) -> None:
    """Inserts one Notification row per admin user, selected by the coarse
    role column — an unconditional broadcast to every admin regardless of
    what they're permissioned for. Correct for genuinely admin-wide events
    (new user registered, QC scan found issues). For anything a specific
    permission is meant to gate who can act on, use
    notify_admins_with_permission instead — mixing the two selections for
    the same event is what caused BODP's double-notification bug (see
    requests_service.create_request's git history). Caller is responsible
    for committing (matches this codebase's convention of committing once
    per logical operation, not per side-effect)."""
    result = await db.execute(select(User.id).where(User.role == UserRoleEnum.ADMIN.value))
    admin_ids = result.scalars().all()
    for admin_id in admin_ids:
        db.add(Notification(user_id=admin_id, type=type, title=title, description=description))


async def notify_admins_with_permission(
    db: AsyncSession, *, permission: str, type: str, title: str, description: str
) -> None:
    """Inserts one Notification row per admin holding the given fine-grained
    permission — for events where only some admins can actually act (e.g.
    "Manage Support" admins for a new contact submission), not a blanket
    broadcast. Deliberately does NOT also select by coarse role=='admin' —
    see notify_admins' docstring for why mixing the two selections for one
    event double-notifies. Caller is responsible for committing."""
    result = await db.execute(
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.permissions.any(permission))
        .distinct()
    )
    admin_ids = result.scalars().all()
    for admin_id in admin_ids:
        db.add(Notification(user_id=admin_id, type=type, title=title, description=description))
