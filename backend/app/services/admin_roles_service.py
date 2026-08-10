import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditActionType
from app.models.user import Role, User, UserRole
from app.schemas.admin_roles import RoleCreate, RoleUpdate
from app.services.audit_service import write_audit_log

# The two roles the seeding migration guarantees exist in every environment
# — protected from deletion since permission-gated endpoints (including
# this router's own) rely on "Administrator" existing and holding every
# permission.
_SYSTEM_ROLE_NAMES = {"Administrator", "User"}


async def list_roles(db: AsyncSession) -> list[tuple[Role, int]]:
    result = await db.execute(
        select(Role, func.count(UserRole.id))
        .outerjoin(UserRole, UserRole.role_id == Role.id)
        .group_by(Role.id)
        .order_by(Role.name)
    )
    return [(row[0], row[1]) for row in result.all()]


async def get_role(db: AsyncSession, role_id: uuid.UUID) -> Role:
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def _user_count(db: AsyncSession, role_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id)
    )
    return result.scalar_one()


async def create_role(
    db: AsyncSession, *, payload: RoleCreate, actor: User, ip_address: str | None
) -> Role:
    role = Role(name=payload.name, description=payload.description, permissions=payload.permissions)
    db.add(role)
    try:
        await db.flush()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A role with this name already exists") from exc

    await write_audit_log(
        db,
        actor=actor,
        action="Created role",
        action_type=AuditActionType.USER,
        target=role.name,
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(role)
    return role


async def update_role(
    db: AsyncSession, *, role: Role, payload: RoleUpdate, actor: User, ip_address: str | None
) -> Role:
    updates = payload.model_dump(exclude_unset=True)

    # "Administrator" is matched by exact name elsewhere (assign_role's
    # coarse-role sync, this module's own delete protection) — renaming it
    # would silently break that without any error here, so block it the
    # same way delete_role already blocks deleting it.
    if (
        role.name in _SYSTEM_ROLE_NAMES
        and "name" in updates
        and updates["name"] != role.name
    ):
        raise HTTPException(status_code=409, detail="Cannot rename a system-seeded role")

    for field, value in updates.items():
        setattr(role, field, value)

    await write_audit_log(
        db,
        actor=actor,
        action="Updated role",
        action_type=AuditActionType.USER,
        target=role.name,
        ip_address=ip_address,
    )
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A role with this name already exists") from exc
    await db.refresh(role)
    return role


async def delete_role(db: AsyncSession, *, role: Role, actor: User, ip_address: str | None) -> None:
    if role.name in _SYSTEM_ROLE_NAMES:
        raise HTTPException(status_code=409, detail="Cannot delete a system-seeded role")

    affected_users = await _user_count(db, role.id)
    await write_audit_log(
        db,
        actor=actor,
        action=f"Deleted role — {affected_users} user(s) lost these permissions",
        action_type=AuditActionType.USER,
        target=role.name,
        ip_address=ip_address,
    )
    # UserRole.role_id's ondelete="CASCADE" removes assignments automatically.
    await db.delete(role)
    await db.commit()
