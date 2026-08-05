from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User, UserRole, UserRoleEnum


async def _get_user_permissions(user: User, db: AsyncSession) -> set[str]:
    """Union of all fine-grained permissions across every Role assigned to this user."""
    result = await db.execute(
        select(UserRole).where(UserRole.user_id == user.id).options()
    )
    user_roles = result.scalars().all()
    if not user_roles:
        return set()

    from app.models.user import Role

    role_ids = [ur.role_id for ur in user_roles]
    roles_result = await db.execute(select(Role).where(Role.id.in_(role_ids)))
    roles = roles_result.scalars().all()

    permissions: set[str] = set()
    for role in roles:
        permissions.update(role.permissions or [])
    return permissions


def require_permission(permission: str) -> Callable:
    """FastAPI dependency factory enforcing a fine-grained permission server-side.

    Coarse `role == "admin"` alone is NOT sufficient to call endpoints
    protected by this dependency — the admin must also hold a fine-grained
    Role that grants the named permission. This is what makes the Roles &
    Permissions admin screen a real authorization boundary rather than a
    decorative UI, per Master Plan §0.

    A user with the base "user" role is always rejected regardless of any
    permission grant — permissions only ever apply within the admin surface.
    """

    async def _dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if current_user.role != UserRoleEnum.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )
        user_permissions = await _get_user_permissions(current_user, db)
        if permission not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission}",
            )
        return current_user

    return _dependency


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Coarse admin gate for endpoints that don't map to one of the 8 named
    permissions (e.g. viewing the admin overview dashboard itself)."""
    if current_user.role != UserRoleEnum.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
