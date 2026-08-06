import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_about_team import (
    AboutTeamMemberCreate,
    AboutTeamMemberPublic,
    AboutTeamMemberUpdate,
)
from app.services import admin_about_team_service
from app.services.admin_media_service import media_url

router = APIRouter(prefix="/admin/about-team", tags=["admin-about-team"])


def _to_public(member) -> AboutTeamMemberPublic:
    return AboutTeamMemberPublic(
        id=member.id,
        name=member.name,
        role=member.role,
        bio=member.bio,
        photo_id=member.photo_id,
        photo_url=media_url(member.photo) if member.photo else None,
        display_order=member.display_order,
    )


@router.get("", response_model=list[AboutTeamMemberPublic])
async def list_members(
    current_user: User = Depends(require_permission("Manage CMS")),
    db: AsyncSession = Depends(get_db),
):
    members = await admin_about_team_service.list_members(db)
    return [_to_public(m) for m in members]


@router.post("", response_model=AboutTeamMemberPublic, status_code=status.HTTP_201_CREATED)
async def create_member(
    payload: AboutTeamMemberCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage CMS")),
    db: AsyncSession = Depends(get_db),
):
    member = await admin_about_team_service.create_member(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_public(member)


@router.patch("/{member_id}", response_model=AboutTeamMemberPublic)
async def update_member(
    member_id: uuid.UUID,
    payload: AboutTeamMemberUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Manage CMS")),
    db: AsyncSession = Depends(get_db),
):
    member = await admin_about_team_service.get_member(db, member_id)
    member = await admin_about_team_service.update_member(
        db, member=member, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_public(member)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    member_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage CMS")),
    db: AsyncSession = Depends(get_db),
):
    member = await admin_about_team_service.get_member(db, member_id)
    await admin_about_team_service.delete_member(
        db, member=member, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
