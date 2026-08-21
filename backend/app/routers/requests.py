import json
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_client_ip, get_current_user
from app.core.limiter import limiter
from app.models.requests import AccessGrant, DatasetRequest, DownloadLog, ExtractionStatus, GrantStatus
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
from app.services.supporting_document_service import (
    SupportingDocumentUploadError,
    upload_supporting_document,
    validate_supporting_document_extension,
)
from app.worker.tasks.notifications import send_request_submitted

router = APIRouter(tags=["requests"])


@router.post("/requests", response_model=RequestSummary, status_code=201)
@limiter.limit(settings.RATE_LIMIT_MUTATIONS)
async def submit_request(
    request: Request,
    dataset_id: uuid.UUID = Form(...),
    justification: str = Form(...),
    search_criteria: str | None = Form(default=None),
    file: UploadFile | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dataset access request submission (Master Plan §3 Phase 4 task 1) —
    from the catalog detail page's "Request Access" modal. `file` (an
    optional supporting document) is validated, stored via
    supporting_document_service, and linked to the created DatasetRequest
    via RequestSupportingDocument (see app/models/requests.py)."""
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

    # Validate the document BEFORE creating the request, so a rejected
    # file (bad type/too large) never leaves a request behind with a
    # missing attachment — the request only starts existing once we know
    # the document (if any) is acceptable.
    if file is not None and file.filename:
        try:
            validate_supporting_document_extension(file.filename)
        except SupportingDocumentUploadError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    request = await requests_service.create_request(
        db,
        user=current_user,
        dataset_id=dataset_id,
        justification=justification,
        search_criteria=criteria,
    )

    if file is not None and file.filename:
        # Captured before any commit/rollback below can expire `request`'s
        # attributes — reading request.id afterward would trigger an
        # implicit synchronous lazy-load, which AsyncSession cannot do.
        request_id = request.id
        try:
            document = await upload_supporting_document(
                db,
                request_id=request_id,
                filename=file.filename,
                file_stream=file,
                uploaded_by=current_user.id,
            )
            request.supporting_document_id = document.id
            await db.commit()
        except SupportingDocumentUploadError as exc:
            # The document could not be stored/linked — the request must
            # not appear successfully submitted while its document is
            # missing, so the request itself is rolled back too (no
            # orphaned request, no orphaned storage object).
            await db.rollback()
            request_row = await db.get(DatasetRequest, request_id)
            if request_row is not None:
                await db.delete(request_row)
                await db.commit()
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

        request = await requests_service.get_request_for_admin(db, request_id)

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
@limiter.limit(settings.RATE_LIMIT_MUTATIONS)
async def extract_grant_subset(
    request: Request,
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
