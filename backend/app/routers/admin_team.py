import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User, UserRoleEnum
from app.schemas.admin_team import AdminInviteCreate, AdminTeamMemberPublic
from app.services import admin_team_service
from app.services.admin_users_service import get_user_for_admin

router = APIRouter(prefix="/admin/team", tags=["admin-team"])


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


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_admin(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await get_user_for_admin(db, user_id)
    if user.role != UserRoleEnum.ADMIN.value:
        raise HTTPException(status_code=404, detail="Admin not found")
    await admin_team_service.remove_admin(
        db, user=user, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
