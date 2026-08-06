import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_team import AdminTeamMemberCreate, AdminTeamMemberPublic, AdminTeamMemberUpdate
from app.services import admin_team_service

router = APIRouter(prefix="/admin/team", tags=["admin-team"])


@router.get("", response_model=list[AdminTeamMemberPublic])
async def list_admin_team(
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_team_service.list_admin_team(db)


@router.post("", response_model=AdminTeamMemberPublic, status_code=status.HTTP_201_CREATED)
async def invite_admin(
    payload: AdminTeamMemberCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_team_service.invite_admin(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )


@router.patch("/{member_id}", response_model=AdminTeamMemberPublic)
async def update_admin_team_member(
    member_id: uuid.UUID,
    payload: AdminTeamMemberUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    member = await admin_team_service.get_admin_team_member(db, member_id)
    return await admin_team_service.update_admin_team_member(
        db, member=member, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_admin_team_member(
    member_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    member = await admin_team_service.get_admin_team_member(db, member_id)
    await admin_team_service.remove_admin_team_member(
        db, member=member, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
