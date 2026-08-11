import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.requests import GrantStatus
from app.models.user import Role, User, UserStatus
from app.schemas.admin_users import (
    AdminUserCreate,
    AdminUserDetail,
    AdminUserSummary,
    AdminUserUpdate,
)
from app.schemas.requests import GrantSummary
from app.services import admin_users_service, requests_service

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


async def _to_detail(db: AsyncSession, user: User, *, email_sent: bool | None = None) -> AdminUserDetail:
    fine_grained_roles = await admin_users_service.get_fine_grained_roles(db, user.id)
    return AdminUserDetail(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        institution=user.institution,
        phone=user.phone,
        role=user.role,
        status=user.status,
        datasets_granted=user.datasets_granted or 0,
        email_verified_at=user.email_verified_at,
        bio=user.bio,
        research_area=user.research_area,
        deletion_requested_at=user.deletion_requested_at,
        created_at=user.created_at,
        fine_grained_roles=fine_grained_roles,
        email_sent=email_sent,
    )


@router.get("", response_model=list[AdminUserSummary])
async def list_users(
    search: str | None = None,
    status_filter: str | None = None,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    users = await admin_users_service.list_users_for_admin(db, search=search, status_filter=status_filter)
    return users


@router.get("/export.csv")
async def export_users_csv(
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    csv_text = await admin_users_service.export_users_csv(db)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bodp_users.csv"},
    )


@router.get("/deletion-requests/pending", response_model=list[AdminUserSummary])
async def list_deletion_requests(
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    # Must be registered before GET /{user_id} — otherwise Starlette
    # matches "deletion-requests" as a user_id path param first (see
    # /export.csv above for the same precedent).
    return await admin_users_service.list_pending_deletions(db)


@router.get("/{user_id}", response_model=AdminUserDetail)
async def get_user(
    user_id: uuid.UUID,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await admin_users_service.get_user_for_admin(db, user_id)
    return await _to_detail(db, user)


@router.post("", response_model=AdminUserDetail, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminUserCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user, email_sent = await admin_users_service.create_user_via_invite(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, user, email_sent=email_sent)


@router.patch("/{user_id}", response_model=AdminUserDetail)
async def update_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await admin_users_service.get_user_for_admin(db, user_id)
    user = await admin_users_service.update_user(
        db, user=user, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, user)


@router.post("/{user_id}/suspend", response_model=AdminUserDetail)
async def suspend_user(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await admin_users_service.get_user_for_admin(db, user_id)
    user = await admin_users_service.set_user_status(
        db,
        user=user,
        status=UserStatus.SUSPENDED.value,
        actor=current_user,
        ip_address=get_client_ip(request),
    )
    return await _to_detail(db, user)


@router.post("/{user_id}/activate", response_model=AdminUserDetail)
async def activate_user(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await admin_users_service.get_user_for_admin(db, user_id)
    user = await admin_users_service.set_user_status(
        db, user=user, status=UserStatus.ACTIVE.value, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    """Permanently deletes an account (anonymizes the row — see
    admin_users_service.anonymize_user's docstring for why this isn't a
    hard DELETE). Distinct from /suspend, which is reversible."""
    user = await admin_users_service.get_user_for_admin(db, user_id)
    if user.id == current_user.id:
        raise HTTPException(status_code=409, detail="You cannot delete your own account")
    await admin_users_service.anonymize_user(
        db, user=user, actor=current_user, ip_address=get_client_ip(request)
    )
    return None


@router.post("/{user_id}/deletion-requests/cancel", response_model=AdminUserDetail)
async def cancel_deletion_request(
    user_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await admin_users_service.get_user_for_admin(db, user_id)
    user = await admin_users_service.cancel_deletion_request(
        db, user=user, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, user)


@router.get("/{user_id}/grants", response_model=list[GrantSummary])
async def get_user_grants(
    user_id: uuid.UUID,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    await admin_users_service.get_user_for_admin(db, user_id)
    return await requests_service.list_grants_for_user(db, user_id)


@router.post("/{user_id}/grants/{grant_id}/revoke", response_model=GrantSummary)
async def revoke_user_grant(
    user_id: uuid.UUID,
    grant_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    grant = await requests_service.get_grant_for_admin(db, grant_id)
    if grant.user_id != user_id:
        raise HTTPException(status_code=404, detail="Grant not found for this user")
    if grant.status != GrantStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail=f"Grant is already {grant.status}")
    return await requests_service.revoke_grant(
        db, grant=grant, admin=current_user, ip_address=get_client_ip(request)
    )


@router.post("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def assign_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await admin_users_service.get_user_for_admin(db, user_id)
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    await admin_users_service.assign_role(
        db, user=user, role=role, actor=current_user, ip_address=get_client_ip(request)
    )
    return None


@router.delete("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Users")),
    db: AsyncSession = Depends(get_db),
):
    user = await admin_users_service.get_user_for_admin(db, user_id)
    role = await db.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    await admin_users_service.unassign_role(
        db, user=user, role=role, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
