import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.requests import (
    ApproveRequestBody,
    ExtendGrantBody,
    GrantDetail,
    ModifyRequestBody,
    RejectRequestBody,
    RequestDetail,
)
from app.services import requests_service
from app.worker.tasks.notifications import send_request_approved, send_request_rejected

router = APIRouter(prefix="/admin", tags=["admin-requests"])

_APPROVE_PERMISSION = "Approve Requests"


@router.get("/requests", response_model=list[RequestDetail])
async def list_requests(
    status: str | None = None,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    return await requests_service.list_requests_for_admin(db, status_filter=status)


@router.patch("/requests/{request_id}/modify", response_model=RequestDetail)
async def modify_request(
    request_id: uuid.UUID,
    body: ModifyRequestBody,
    request: Request,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    """Save Changes — persists an admin's edited filter configuration
    without approving or rejecting. Reuses the same "Approve Requests"
    permission as approve/reject, since reviewing/modifying a request's
    scope is part of the same review capability, not a separate one."""
    dataset_request = await requests_service.get_request_for_admin(db, request_id)
    modified = await requests_service.modify_request(
        db,
        request=dataset_request,
        admin=current_user,
        search_criteria=body.search_criteria,
        ip_address=get_client_ip(request),
    )
    return modified


@router.post("/requests/{request_id}/approve", response_model=GrantDetail)
async def approve_request(
    request_id: uuid.UUID,
    body: ApproveRequestBody,
    request: Request,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    dataset_request = await requests_service.get_request_for_admin(db, request_id)
    grant = await requests_service.approve_request(
        db,
        request=dataset_request,
        admin=current_user,
        search_criteria_override=body.search_criteria,
        note=body.note,
        duration=body.duration,
        custom_expires_at=body.custom_expires_at,
        ip_address=get_client_ip(request),
    )
    send_request_approved.delay(str(dataset_request.id))
    return await _to_grant_detail(db, grant)


@router.post("/requests/{request_id}/reject", response_model=RequestDetail)
async def reject_request(
    request_id: uuid.UUID,
    body: RejectRequestBody,
    request: Request,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    dataset_request = await requests_service.get_request_for_admin(db, request_id)
    rejected = await requests_service.reject_request(
        db,
        request=dataset_request,
        admin=current_user,
        reason=body.reason,
        ip_address=get_client_ip(request),
    )
    send_request_rejected.delay(str(rejected.id))
    return rejected


@router.get("/grants", response_model=list[GrantDetail])
async def list_grants(
    status: str | None = None,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    grants = await requests_service.list_grants_for_admin(db, status_filter=status)
    return [await _to_grant_detail(db, g) for g in grants]


@router.post("/grants/{grant_id}/extend", response_model=GrantDetail)
async def extend_grant(
    grant_id: uuid.UUID,
    body: ExtendGrantBody,
    request: Request,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    grant = await requests_service.get_grant_for_admin(db, grant_id)
    extended = await requests_service.extend_grant(
        db,
        grant=grant,
        admin=current_user,
        duration=body.duration,
        custom_expires_at=body.custom_expires_at,
        ip_address=get_client_ip(request),
    )
    return await _to_grant_detail(db, extended)


@router.post("/grants/{grant_id}/revoke", response_model=GrantDetail)
async def revoke_grant(
    grant_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    grant = await requests_service.get_grant_for_admin(db, grant_id)
    revoked = await requests_service.revoke_grant(
        db, grant=grant, admin=current_user, ip_address=get_client_ip(request)
    )
    return await _to_grant_detail(db, revoked)


async def _to_grant_detail(db: AsyncSession, grant) -> GrantDetail:
    granted_by_name = None
    if grant.granted_by is not None:
        granter = await db.get(User, grant.granted_by)
        granted_by_name = granter.full_name if granter else None
    return GrantDetail(
        id=grant.id,
        dataset=grant.dataset,
        granted_at=grant.granted_at,
        expires_at=grant.expires_at,
        status=grant.status,
        scope=grant.scope,
        user=grant.user,
        granted_by_name=granted_by_name,
    )
