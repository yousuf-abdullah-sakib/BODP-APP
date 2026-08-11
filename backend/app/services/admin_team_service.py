import secrets
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_invite_token, hash_password
from app.models.audit import ActiveSession, AuditActionType
from app.models.user import Role, User, UserRole, UserRoleEnum, UserStatus
from app.schemas.admin_team import AdminInviteCreate
from app.services.audit_service import write_audit_log
from app.services.email_service import email_service


async def _roles_by_user(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not user_ids:
        return {}
    result = await db.execute(
        select(UserRole.user_id, Role.name)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(user_ids))
        .order_by(Role.name)
    )
    roles_by_user: dict[uuid.UUID, list[str]] = {}
    for user_id, role_name in result.all():
        roles_by_user.setdefault(user_id, []).append(role_name)
    return roles_by_user


async def _last_active_by_user(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
    if not user_ids:
        return {}
    result = await db.execute(
        select(ActiveSession.user_id, func.max(ActiveSession.last_active_at))
        .where(ActiveSession.user_id.in_(user_ids))
        .group_by(ActiveSession.user_id)
    )
    return dict(result.all())


async def list_admin_team(db: AsyncSession) -> list[dict]:
    """Admin Management's roster, derived directly from real User + Role
    data — not a separate table. Every user with coarse role='admin' is an
    admin-panel account by definition (see admin_users_service.assign_role,
    which is what actually grants this)."""
    result = await db.execute(
        select(User).where(User.role == UserRoleEnum.ADMIN.value).order_by(User.full_name)
    )
    users = list(result.scalars().all())
    user_ids = [u.id for u in users]

    roles_by_user = await _roles_by_user(db, user_ids)
    last_active_by_user = await _last_active_by_user(db, user_ids)

    return [
        {
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "status": u.status,
            "roles": roles_by_user.get(u.id, []),
            "last_active_at": last_active_by_user.get(u.id),
            "created_at": u.created_at,
            "email_sent": None,
        }
        for u in users
    ]


async def get_admin_team_member(db: AsyncSession, user_id: uuid.UUID) -> dict:
    user = await db.get(User, user_id)
    if user is None or user.role != UserRoleEnum.ADMIN.value:
        raise HTTPException(status_code=404, detail="Admin not found")

    roles_by_user = await _roles_by_user(db, [user_id])
    last_active_by_user = await _last_active_by_user(db, [user_id])
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "status": user.status,
        "roles": roles_by_user.get(user_id, []),
        "last_active_at": last_active_by_user.get(user_id),
        "created_at": user.created_at,
        "email_sent": None,
    }


async def invite_admin(
    db: AsyncSession, *, payload: AdminInviteCreate, actor: User, ip_address: str | None
) -> dict:
    normalized_email = payload.email.strip().lower()
    existing = await db.execute(select(User).where(User.email == normalized_email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    role = await db.get(Role, payload.role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    user = User(
        email=normalized_email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        full_name=payload.full_name.strip(),
        # A non-"User" Role is what actually grants admin-panel access
        # (see admin_users_service.assign_role's coarse-role sync) — set
        # it directly here up front rather than inviting as role="user"
        # and immediately flipping it, since both happen in one request
        # anyway.
        role=UserRoleEnum.ADMIN.value if role.name != "User" else UserRoleEnum.USER.value,
        status=UserStatus.ACTIVE.value,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()

    db.add(UserRole(user_id=user.id, role_id=role.id))

    await write_audit_log(
        db,
        actor=actor,
        action=f"Invited admin with role '{role.name}'",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()

    token = create_invite_token(str(user.id), user.email)
    email_sent = email_service.send_admin_invite_email(user.email, user.full_name, token, is_admin=True)

    member = await get_admin_team_member(db, user.id)
    member["email_sent"] = email_sent
    return member


async def resend_invite(db: AsyncSession, *, user: User) -> bool:
    """Re-sends the set-password email with a fresh token — for when the
    original send failed (e.g. a transient SMTP/provider outage) and the
    admin needs a way to retry without re-creating the account."""
    token = create_invite_token(str(user.id), user.email)
    return email_service.send_admin_invite_email(user.email, user.full_name, token, is_admin=True)


async def suspend_admin(db: AsyncSession, *, user: User, actor: User, ip_address: str | None) -> None:
    # Reversible — revokes admin access immediately while keeping the
    # account and its Role assignments intact, so reactivating restores
    # exactly what they had before with no re-assignment needed. Distinct
    # from remove_admin, which is permanent.
    user.status = UserStatus.SUSPENDED.value

    await write_audit_log(
        db,
        actor=actor,
        action="Suspended admin — access revoked",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()


async def reactivate_admin(db: AsyncSession, *, user: User, actor: User, ip_address: str | None) -> None:
    user.status = UserStatus.ACTIVE.value

    await write_audit_log(
        db,
        actor=actor,
        action="Reactivated admin",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()
