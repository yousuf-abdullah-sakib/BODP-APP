import json
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_client_ip, get_current_user
from app.models.requests import AccessGrant, DownloadLog, ExtractionStatus, GrantStatus
from app.models.user import User
from app.schemas.requests import (
    ExtractionCreate,
    ExtractionStatusResponse,
    GrantSummary,
    RequestSummary,
    SearchCriteriaSchema,
)
from app.services import extraction_service, requests_service
from app.services.storage.registry import get_storage_backend
from app.worker.tasks.notifications import send_request_submitted

router = APIRouter(tags=["requests"])


@router.post("/requests", response_model=RequestSummary, status_code=201)
async def submit_request(
    dataset_id: uuid.UUID = Form(...),
    justification: str = Form(...),
    search_criteria: str | None = Form(default=None),
    file: UploadFile | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dataset access request submission (Master Plan §3 Phase 4 task 1) —
    from the catalog detail page's "Request Access" modal. `file` (an
    optional supporting document) is accepted but not persisted in this
    phase: DatasetRequest.supporting_document_file_id targets dataset_files,
    which is shaped for scientific data files, not request attachments — a
    mismatched fit. The Master Plan marks the attachment as optional, so this
    is a deliberate simplification rather than a missing feature."""
    if len(justification.strip()) < 50:
        raise HTTPException(
            status_code=422, detail="Justification must be at least 50 characters."
        )

    criteria = None
    if search_criteria:
        try:
            criteria = SearchCriteriaSchema.model_validate(json.loads(search_criteria))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid search_criteria") from exc

    request = await requests_service.create_request(
        db,
        user=current_user,
        dataset_id=dataset_id,
        justification=justification,
        search_criteria=criteria,
    )

    send_request_submitted.delay(str(request.id))

    return request


@router.get("/me/requests", response_model=list[RequestSummary])
async def my_requests(
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await requests_service.list_requests_for_user(
        db, current_user.id, status_filter=status
    )


@router.get("/me/grants", response_model=list[GrantSummary])
async def my_grants(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await requests_service.list_grants_for_user(db, current_user.id)


async def _get_owned_grant(db: AsyncSession, grant_id: uuid.UUID, user_id: uuid.UUID) -> AccessGrant:
    grant = await db.get(AccessGrant, grant_id)
    if grant is None or grant.user_id != user_id:
        raise HTTPException(status_code=404, detail="Grant not found")
    return grant


@router.post("/me/grants/{grant_id}/extract", response_model=ExtractionStatusResponse, status_code=202)
async def extract_grant_subset(
    grant_id: uuid.UUID,
    body: ExtractionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Starts a scope-filtered extraction of a grant's dataset (Master Plan
    §3 Phase 5 task 1). Small results complete synchronously (task 5);
    larger ones return 'queued' for the client to poll."""
    grant = await _get_owned_grant(db, grant_id, current_user.id)
    extraction = await extraction_service.create_extraction(
        db, grant=grant, requested_scope=body.scope, output_format=body.format.value
    )
    return extraction


@router.get("/me/extractions/{extraction_id}", response_model=ExtractionStatusResponse)
async def get_extraction_status(
    extraction_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await extraction_service.get_extraction_for_user(db, extraction_id, current_user.id)


@router.get("/me/extractions/{extraction_id}/download")
async def download_extraction(
    extraction_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tracking endpoint in front of the presigned URL (Master Plan §3 Phase
    5 task 4) — a download_logs row is written here, on actual retrieval,
    not when the extraction merely completes. Returns the presigned URL as
    JSON rather than an HTTP redirect so the frontend can fetch it via its
    normal authenticated apiFetch client, then navigate the browser to the
    (self-authenticating, signed) URL directly."""
    extraction = await extraction_service.get_extraction_for_user(db, extraction_id, current_user.id)
    if extraction.status != ExtractionStatus.COMPLETE.value:
        raise HTTPException(status_code=409, detail=f"Extraction is {extraction.status}, not ready for download")

    grant = await db.get(AccessGrant, extraction.grant_id)
    if grant is None or grant.status != GrantStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail="Grant is no longer active")

    db.add(
        DownloadLog(
            grant_id=extraction.grant_id,
            user_id=current_user.id,
            subset_extraction_id=extraction.id,
            ip_address=get_client_ip(request),
        )
    )
    await db.commit()

    storage = get_storage_backend(extraction.output_storage_backend)
    download_url = storage.presign_get(
        extraction.output_bucket,
        extraction.output_file_key,
        expires_in_seconds=settings.EXTRACTION_DOWNLOAD_URL_EXPIRE_MINUTES * 60,
    )
    return {"download_url": download_url}
