import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User, UserRoleEnum
from app.schemas.admin_team import AdminInviteCreate, AdminTeamMemberPublic
from app.services import admin_team_service, admin_users_service
from app.services.admin_users_service import get_user_for_admin

router = APIRouter(prefix="/admin/team", tags=["admin-team"])


def _require_admin_user(user: User) -> None:
    if user.role != UserRoleEnum.ADMIN.value:
        raise HTTPException(status_code=404, detail="Admin not found")


@router.get("", response_model=list[AdminTeamMemberPublic])
async def list_admin_team(
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_team_service.list_admin_team(db)


@router.post("", response_model=AdminTeamMemberPublic, status_code=status.HTTP_201_CREATED)
async def invite_admin(
    payload: AdminInviteCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_team_service.invite_admin(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )


@router.post("/{user_id}/resend-invite")
async def resend_invite(
    user_id: uuid.UUID,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_for_admin(db, user_id)
    _require_admin_user(user)
    email_sent = await admin_team_service.resend_invite(db, user=user)
    return {"email_sent": email_sent}


@router.post("/{user_id}/suspend", response_model=AdminTeamMemberPublic)
async def suspend_admin(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    """Reversible — the account and its Roles stay intact; see /reactivate."""
    user = await get_user_for_admin(db, user_id)
    _require_admin_user(user)
    await admin_team_service.suspend_admin(
        db, user=user, actor=current_user, ip_address=get_client_ip(request)
    )
    return await admin_team_service.get_admin_team_member(db, user_id)


@router.post("/{user_id}/reactivate", response_model=AdminTeamMemberPublic)
async def reactivate_admin(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_for_admin(db, user_id)
    _require_admin_user(user)
    await admin_team_service.reactivate_admin(
        db, user=user, actor=current_user, ip_address=get_client_ip(request)
    )
    return await admin_team_service.get_admin_team_member(db, user_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_admin(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    """Permanently deletes the account (anonymizes the row — see
    admin_users_service.anonymize_user). Distinct from /suspend, which is
    reversible. This is the same underlying operation as User
    Management's DELETE /admin/users/{id} — an admin account is still a
    User row."""
    user = await get_user_for_admin(db, user_id)
    _require_admin_user(user)
    if user.id == current_user.id:
        raise HTTPException(status_code=409, detail="You cannot delete your own account")
    await admin_users_service.anonymize_user(
        db, user=user, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
