import csv
import io
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import create_invite_token, hash_password
from app.models.audit import ActiveSession, AuditActionType
from app.models.user import Role, User, UserRole, UserRoleEnum, UserStatus
from app.schemas.admin_users import AdminUserCreate, AdminUserUpdate
from app.services.audit_service import write_audit_log
from app.services.email_service import email_service


async def list_users_for_admin(
    db: AsyncSession, *, search: str | None = None, status_filter: str | None = None
) -> list[User]:
    # Researcher accounts only — admins are managed separately via Admin
    # Team, matching the prototype's own Users-section filtering.
    query = select(User).where(User.role == UserRoleEnum.USER.value).order_by(User.created_at.desc())
    if status_filter:
        query = query.where(User.status == status_filter)
    else:
        # Anonymized ("Deleted User") rows only ever surface when an admin
        # explicitly filters for status=deleted — the default view would
        # otherwise fill up with rows that no longer represent a real,
        # actionable account.
        query = query.where(User.status != UserStatus.DELETED.value)
    if search:
        query = query.where(User.full_name.ilike(f"%{search}%") | User.email.ilike(f"%{search}%"))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_user_for_admin(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def get_fine_grained_roles(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    result = await db.execute(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.name)
    )
    return list(result.scalars().all())


async def create_user_via_invite(
    db: AsyncSession, *, payload: AdminUserCreate, actor: User, ip_address: str | None
) -> tuple[User, bool]:
    normalized_email = payload.email.strip().lower()
    existing = await db.execute(select(User).where(User.email == normalized_email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(
        email=normalized_email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        full_name=payload.full_name.strip(),
        institution=payload.institution,
        phone=payload.phone,
        role=UserRoleEnum.USER.value,
        status=UserStatus.ACTIVE.value,
        # The admin vouches for this email address — no separate
        # email-ownership verification stacked on top of invite/set-password.
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_invite_token(str(user.id), user.email)
    email_sent = email_service.send_admin_invite_email(user.email, user.full_name, token, is_admin=False)

    await write_audit_log(
        db,
        actor=actor,
        action="Created user (invited)",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()
    return user, email_sent


async def update_user(
    db: AsyncSession, *, user: User, payload: AdminUserUpdate, actor: User, ip_address: str | None
) -> User:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    await write_audit_log(
        db,
        actor=actor,
        action="Updated user",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(user)
    return user


_STATUS_VERBS = {
    UserStatus.SUSPENDED.value: "Suspended",
    UserStatus.ACTIVE.value: "Activated",
}


async def set_user_status(
    db: AsyncSession, *, user: User, status: str, actor: User, ip_address: str | None
) -> User:
    user.status = status

    verb = _STATUS_VERBS.get(status, "Updated status for")
    await write_audit_log(
        db,
        actor=actor,
        action=f"{verb} user",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(user)
    return user


async def anonymize_user(
    db: AsyncSession, *, user: User, actor: User, ip_address: str | None
) -> None:
    """Permanently deletes an account's identifying information while
    keeping the row (and everything that references it — dataset requests,
    grants, audit log entries, support tickets, etc.) intact for history.
    The real email is freed for reuse (a new registration or a fresh admin
    invite can claim it immediately) since it's rewritten to a unique
    placeholder rather than cleared to NULL, which the `email` column
    doesn't allow.

    Deliberately NOT a hard `DELETE FROM users` — see Master Plan/session
    notes: cascading a real delete through 21 FKs on users.id would erase
    audit-log actor attribution and a meaningful slice of platform history
    with no undo. Anonymizing achieves the actual requirement ("that email
    should become available again") without that blast radius.
    """
    original_email = user.email
    original_name = user.full_name

    # Revoke every active session first — anonymizing out from under a
    # logged-in session would otherwise leave a valid access token
    # attached to a row that no longer has a real identity.
    result = await db.execute(
        select(ActiveSession).where(
            ActiveSession.user_id == user.id, ActiveSession.revoked_at.is_(None)
        )
    )
    for session in result.scalars().all():
        session.revoked_at = datetime.now(UTC)

    # Every UserRole assignment is removed — a deleted account holds no
    # roles/permissions, admin or otherwise.
    role_rows = await db.execute(select(UserRole).where(UserRole.user_id == user.id))
    for user_role in role_rows.scalars().all():
        await db.delete(user_role)

    user.email = f"deleted-{user.id}@deleted.bodp.invalid"
    user.full_name = "Deleted User"
    user.password_hash = hash_password(secrets.token_urlsafe(32))
    user.institution = None
    user.phone = None
    user.bio = None
    user.research_area = None
    user.avatar_key = None
    user.status = UserStatus.DELETED.value
    user.role = UserRoleEnum.USER.value
    user.deletion_requested_at = None

    await write_audit_log(
        db,
        actor=actor,
        action="Permanently deleted account",
        action_type=AuditActionType.USER,
        target=f"{original_name} ({original_email})",
        ip_address=ip_address,
    )
    await db.commit()


async def list_pending_deletions(db: AsyncSession) -> list[User]:
    result = await db.execute(
        select(User)
        .where(User.deletion_requested_at.is_not(None))
        .order_by(User.deletion_requested_at)
    )
    return list(result.scalars().all())


async def cancel_deletion_request(
    db: AsyncSession, *, user: User, actor: User, ip_address: str | None
) -> User:
    user.deletion_requested_at = None
    await write_audit_log(
        db,
        actor=actor,
        action="Cancelled account deletion request",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _lock_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Row-locks the User for the rest of this transaction (Postgres
    SELECT ... FOR UPDATE) — assign_role/unassign_role both read-then-write
    User.role based on the current UserRole membership, and two concurrent
    requests for the same user (e.g. a frontend that fires an assign and an
    unassign together when an operator changes someone's role) can
    otherwise interleave: request A's "any roles left?" recheck can run
    before request B's INSERT commits, see none, and demote the user right
    after B just promoted them — last-committer-wins on the User.role
    column with no signal that a concurrent write happened. Locking here
    makes the second request block until the first commits, so it always
    recomputes membership against the other request's already-committed
    result instead of racing it."""
    result = await db.execute(select(User).where(User.id == user_id).with_for_update())
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def assign_role(
    db: AsyncSession, *, user: User, role: Role, actor: User, ip_address: str | None
) -> None:
    user = await _lock_user(db, user.id)

    existing = await db.execute(
        select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id)
    )
    if existing.scalar_one_or_none() is not None:
        return

    db.add(UserRole(user_id=user.id, role_id=role.id))

    # Any Role other than the seeded "User" role grants coarse admin access
    # — require_permission() checks BOTH `user.role == 'admin'` AND the
    # fine-grained permission, so a user assigned e.g. "Data Manager"
    # without also being flipped to the coarse admin role would still get
    # 403'd on every admin-panel endpoint despite holding the right
    # permission. "User" is the one Role that represents a plain
    # researcher account and must never grant admin-panel entry.
    if role.name != "User":
        user.role = UserRoleEnum.ADMIN.value

    await write_audit_log(
        db,
        actor=actor,
        action=f"Assigned role '{role.name}' to user",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()


async def unassign_role(
    db: AsyncSession, *, user: User, role: Role, actor: User, ip_address: str | None
) -> None:
    user = await _lock_user(db, user.id)

    result = await db.execute(
        select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id)
    )
    user_role = result.scalar_one_or_none()
    if user_role is None:
        return

    await db.delete(user_role)

    # Mirror of the sync in assign_role above — losing an admin-panel Role
    # (anything but "User") drops the user back to a plain researcher
    # account, but only once NO admin-panel Role remains (a user can hold
    # more than one, e.g. both "Reviewer" and "Content Editor"). Uses a
    # flush so the DELETE above is visible to this membership re-check
    # rather than counting the row we're in the middle of removing.
    if role.name != "User":
        await db.flush()
        remaining = await db.execute(
            select(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user.id, Role.name != "User")
        )
        if remaining.scalar_one_or_none() is None:
            user.role = UserRoleEnum.USER.value

    await write_audit_log(
        db,
        actor=actor,
        action=f"Removed role '{role.name}' from user",
        action_type=AuditActionType.USER,
        target=f"{user.full_name} ({user.email})",
        ip_address=ip_address,
    )
    await db.commit()


async def export_users_csv(db: AsyncSession) -> str:
    users = await list_users_for_admin(db)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Name", "Email", "Institution", "Role", "Datasets", "Joined", "Status"])
    for user in users:
        writer.writerow(
            [
                user.full_name,
                user.email,
                user.institution or "",
                user.role,
                user.datasets_granted or 0,
                user.created_at.date().isoformat(),
                user.status,
            ]
        )
    return buffer.getvalue()
