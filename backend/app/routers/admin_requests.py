import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.requests import (
    ApproveRequestBody,
    ExtendGrantBody,
    GrantDetail,
    RejectRequestBody,
    RequestDetail,
)
from app.services import requests_service
from app.services.storage.registry import get_storage_backend
from app.services.supporting_document_service import get_supporting_document_for_request
from app.worker.tasks.notifications import send_request_approved, send_request_rejected

router = APIRouter(prefix="/admin", tags=["admin-requests"])

_APPROVE_PERMISSION = "Approve Requests"


def _to_request_detail(request, coverage: dict) -> RequestDetail:
    return RequestDetail.model_validate(request).model_copy(update=coverage)


@router.get("/requests", response_model=list[RequestDetail])
async def list_requests(
    status: str | None = None,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    pairs = await requests_service.list_requests_for_admin(db, status_filter=status)
    return [_to_request_detail(r, coverage) for r, coverage in pairs]


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
    coverage = requests_service.read_request_coverage_snapshot(rejected)
    return _to_request_detail(rejected, coverage)


@router.get("/requests/{request_id}/supporting-document/download")
async def download_supporting_document(
    request_id: uuid.UUID,
    current_user: User = Depends(require_permission(_APPROVE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    """Returns a short-lived presigned URL for an admin to view/download a
    request's supporting document — never a raw storage path/key. Mirrors
    GET /me/extractions/{id}/download's JSON-URL pattern: the frontend
    fetches this via its authenticated client, then navigates the browser
    to the (self-authenticating, signed) URL directly."""
    dataset_request = await requests_service.get_request_for_admin(db, request_id)
    if dataset_request.supporting_document_id is None:
        raise HTTPException(status_code=404, detail="This request has no supporting document")

    document = await get_supporting_document_for_request(
        db, request_id=request_id, document_id=dataset_request.supporting_document_id
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Supporting document not found")

    storage = get_storage_backend(document.storage_backend)
    download_url = storage.presign_get(
        document.storage_bucket,
        document.storage_key,
        expires_in_seconds=settings.EXTRACTION_DOWNLOAD_URL_EXPIRE_MINUTES * 60,
    )
    return {"download_url": download_url, "filename": document.original_filename}


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
