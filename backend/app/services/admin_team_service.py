import secrets
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_invite_token, hash_password
from app.models.admin import AdminTeamMember
from app.models.audit import AuditActionType
from app.models.user import Role, User, UserRole, UserRoleEnum, UserStatus
from app.schemas.admin_team import AdminTeamMemberCreate, AdminTeamMemberUpdate
from app.services.audit_service import write_audit_log
from app.services.email_service import email_service


async def list_admin_team(db: AsyncSession) -> list[AdminTeamMember]:
    result = await db.execute(select(AdminTeamMember).order_by(AdminTeamMember.name))
    return list(result.scalars().all())


async def get_admin_team_member(db: AsyncSession, member_id: uuid.UUID) -> AdminTeamMember:
    member = await db.get(AdminTeamMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Admin team member not found")
    return member


async def invite_admin(
    db: AsyncSession, *, payload: AdminTeamMemberCreate, actor: User, ip_address: str | None
) -> AdminTeamMember:
    normalized_email = payload.email.strip().lower()
    existing = await db.execute(select(User).where(User.email == normalized_email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(
        email=normalized_email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        full_name=payload.name.strip(),
        role=UserRoleEnum.ADMIN.value,
        status=UserStatus.ACTIVE.value,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()

    # The coarse role='admin' above only satisfies HALF of what
    # require_permission() checks — every fine-grained-permission-gated
    # section (the vast majority of the admin panel) also needs the user to
    # hold a Role granting that permission. Without this, an invited admin
    # could sign in but get 403s (surfacing as stuck/failed loads) on nearly
    # every section. Attaching the seeded "Administrator" Role (all
    # permissions) here is what makes "Admin Management" actually invite a
    # fully-privileged administrator, matching what the name implies.
    admin_role = (await db.execute(select(Role).where(Role.name == "Administrator"))).scalar_one_or_none()
    if admin_role is not None:
        db.add(UserRole(user_id=user.id, role_id=admin_role.id))

    member = AdminTeamMember(
        user_id=user.id,
        name=payload.name.strip(),
        email=normalized_email,
        role_label=payload.role_label,
        status="active",
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)

    token = create_invite_token(str(user.id), user.email)
    email_service.send_admin_invite_email(user.email, user.full_name, token, is_admin=True)

    await write_audit_log(
        db,
        actor=actor,
        action="Invited admin team member",
        action_type=AuditActionType.USER,
        target=f"{member.name} ({member.email})",
        ip_address=ip_address,
    )
    await db.commit()
    return member


async def update_admin_team_member(
    db: AsyncSession,
    *,
    member: AdminTeamMember,
    payload: AdminTeamMemberUpdate,
    actor: User,
    ip_address: str | None,
) -> AdminTeamMember:
    updates = payload.model_dump(exclude_unset=True)
    status_changed = "status" in updates
    for field, value in updates.items():
        setattr(member, field, value)

    if status_changed and member.user_id is not None:
        linked_user = await db.get(User, member.user_id)
        if linked_user is not None:
            linked_user.status = member.status

    await write_audit_log(
        db,
        actor=actor,
        action="Updated admin team member",
        action_type=AuditActionType.USER,
        target=f"{member.name} ({member.email})",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(member)
    return member


async def remove_admin_team_member(
    db: AsyncSession, *, member: AdminTeamMember, actor: User, ip_address: str | None
) -> None:
    # Suspend (not delete) the underlying User — revokes admin access
    # immediately while staying non-destructive/reversible, matching how
    # regular-user suspension already works.
    if member.user_id is not None:
        linked_user = await db.get(User, member.user_id)
        if linked_user is not None:
            linked_user.status = UserStatus.SUSPENDED.value

    await write_audit_log(
        db,
        actor=actor,
        action="Removed admin team member — access revoked",
        action_type=AuditActionType.USER,
        target=f"{member.name} ({member.email})",
        ip_address=ip_address,
    )
    await db.delete(member)
    await db.commit()
