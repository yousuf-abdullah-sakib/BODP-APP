import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.uploads import Upload
from app.models.user import User
from app.schemas.admin_datasets import (
    DatasetAdminDetail,
    DatasetAdminSummary,
    DatasetCreate,
    DatasetPermanentDeleteConfirm,
    DatasetUpdate,
)
from app.schemas.datasets import (
    DatasetFilePublic,
    DatasetFileUploadResponse,
    MultipartUploadCompleteResponse,
    MultipartUploadInitiateRequest,
    MultipartUploadInitiateResponse,
    MultipartUploadPartCompleteRequest,
    MultipartUploadPresignPartResponse,
    UploadStatusResponse,
)
from app.services import admin_datasets_service, dataset_multipart_upload_service, settings_service
from app.services.dataset_file_service import DatasetFileUploadError, upload_dataset_file
from app.services.dataset_multipart_upload_service import (
    PART_SIZE_BYTES,
    DatasetMultipartUploadError,
)
from app.worker.celery_app import ingestion_soft_time_limit_seconds
from app.worker.tasks.ingestion import process_dataset_file

router = APIRouter(prefix="/admin/datasets", tags=["admin-datasets"])


def _to_summary(dataset) -> DatasetAdminSummary:
    return DatasetAdminSummary(
        id=dataset.id,
        code=dataset.code,
        title=dataset.title,
        category_name=dataset.category.name if dataset.category else None,
        location=dataset.location,
        record_count=dataset.record_count,
        status=dataset.status,
        updated_at=dataset.updated_at,
    )


async def _to_detail(db: AsyncSession, dataset) -> DatasetAdminDetail:
    active_grant_count = await admin_datasets_service.count_active_grants(db, dataset.id)
    return DatasetAdminDetail(
        id=dataset.id,
        code=dataset.code,
        title=dataset.title,
        description=dataset.description,
        category_id=dataset.category_id,
        category_name=dataset.category.name if dataset.category else None,
        location=dataset.location,
        source=dataset.source,
        platforms=dataset.platforms,
        parameters=dataset.parameters,
        resolution=dataset.resolution,
        license=dataset.license,
        processing_levels=dataset.processing_levels,
        formats=dataset.formats,
        status=dataset.status,
        temporal_start=dataset.temporal_start,
        temporal_end=dataset.temporal_end,
        record_count=dataset.record_count,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
        files=[DatasetFilePublic.model_validate(f) for f in dataset.files],
        active_grant_count=active_grant_count,
    )


@router.get("", response_model=list[DatasetAdminSummary])
async def list_datasets(
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    datasets = await admin_datasets_service.list_datasets_for_admin(
        db, search=search, category_id=category_id, status_filter=status_filter
    )
    return [_to_summary(d) for d in datasets]


@router.get("/uploads/{upload_id}", response_model=UploadStatusResponse)
async def get_upload_status(
    upload_id: uuid.UUID,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Real status polling endpoint (Master Plan §3 Phase 2 deliverable),
    replacing the prototype's hardcoded 2500ms setTimeout — the frontend's
    Data Upload section polls this to reflect actual Celery task state.

    Registered before `/{dataset_id}` so the literal "uploads" path
    segment isn't swallowed by the dataset_id wildcard route."""
    result = await db.execute(select(Upload).where(Upload.id == upload_id))
    upload = result.scalar_one_or_none()
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return upload


@router.get("/{dataset_id}", response_model=DatasetAdminDetail)
async def get_dataset(
    dataset_id: uuid.UUID,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    dataset = await admin_datasets_service.get_dataset_for_admin(db, dataset_id)
    return await _to_detail(db, dataset)


@router.post("", response_model=DatasetAdminDetail, status_code=status.HTTP_201_CREATED)
async def create_dataset_shell(
    payload: DatasetCreate,
    request: Request,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Full admin create form. Also accepts the minimal Phase 2 shape
    (`{title, description}` or `{title, description, code}`) on the same
    route, since every field beyond title/description is optional."""
    dataset = await admin_datasets_service.create_dataset(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, dataset)


@router.patch("/{dataset_id}", response_model=DatasetAdminDetail)
async def update_dataset(
    dataset_id: uuid.UUID,
    payload: DatasetUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    dataset = await admin_datasets_service.get_dataset_for_admin(db, dataset_id)
    dataset = await admin_datasets_service.update_dataset(
        db, dataset=dataset, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, dataset)


@router.post("/{dataset_id}/publish", response_model=DatasetAdminDetail)
async def publish_dataset(
    dataset_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Publish Content")),
    db: AsyncSession = Depends(get_db),
):
    dataset = await admin_datasets_service.get_dataset_for_admin(db, dataset_id)
    dataset = await admin_datasets_service.set_publish_status(
        db, dataset=dataset, published=True, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, dataset)


@router.post("/{dataset_id}/unpublish", response_model=DatasetAdminDetail)
async def unpublish_dataset(
    dataset_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Publish Content")),
    db: AsyncSession = Depends(get_db),
):
    dataset = await admin_datasets_service.get_dataset_for_admin(db, dataset_id)
    dataset = await admin_datasets_service.set_publish_status(
        db, dataset=dataset, published=False, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, dataset)


@router.post("/{dataset_id}/archive", response_model=DatasetAdminDetail)
async def archive_dataset(
    dataset_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Delete Datasets")),
    db: AsyncSession = Depends(get_db),
):
    dataset = await admin_datasets_service.get_dataset_for_admin(db, dataset_id)
    dataset = await admin_datasets_service.archive_dataset(
        db, dataset=dataset, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, dataset)


@router.post("/{dataset_id}/unarchive", response_model=DatasetAdminDetail)
async def unarchive_dataset(
    dataset_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Delete Datasets")),
    db: AsyncSession = Depends(get_db),
):
    dataset = await admin_datasets_service.get_dataset_for_admin(db, dataset_id)
    dataset = await admin_datasets_service.unarchive_dataset(
        db, dataset=dataset, actor=current_user, ip_address=get_client_ip(request)
    )
    return await _to_detail(db, dataset)


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_dataset(
    dataset_id: uuid.UUID,
    payload: DatasetPermanentDeleteConfirm,
    request: Request,
    current_user: User = Depends(require_permission("Delete Datasets")),
    db: AsyncSession = Depends(get_db),
):
    dataset = await admin_datasets_service.get_dataset_for_admin(db, dataset_id)
    await admin_datasets_service.permanently_delete_dataset(
        db, dataset=dataset, actor=current_user, ip_address=get_client_ip(request)
    )
    return None


@router.post(
    "/{dataset_id}/files",
    response_model=DatasetFileUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_dataset_file_endpoint(
    dataset_id: uuid.UUID,
    file: UploadFile,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a raw scientific data file (CSV/NetCDF/.mat) to a dataset.
    Stores the raw file synchronously (streamed, validated, checksummed),
    then dispatches background processing (parse -> extent detection ->
    Parquet conversion) to Celery — matching the prototype's
    queued/processing/complete/failed upload lifecycle, now backed by a real
    task instead of a fixed timer (Master Plan §3 Phase 2 task 7)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    site_settings = await settings_service.get_settings(db)

    try:
        upload, dataset_file = await upload_dataset_file(
            db,
            dataset_id=dataset_id,
            filename=file.filename,
            file_stream=file,
            uploaded_by=current_user.id,
            max_upload_size_mb=site_settings.max_upload_size_mb,
        )
    except DatasetFileUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    task = process_dataset_file.apply_async(
        args=[str(dataset_file.id), str(upload.id)],
        soft_time_limit=ingestion_soft_time_limit_seconds(dataset_file.file_size_bytes),
    )
    upload.celery_task_id = task.id
    await db.commit()
    await db.refresh(upload)
    # In eager mode (tests, or if ever configured for a given deployment)
    # the task above has already run to completion via a separate DB
    # session by this point — refresh so the response reflects it rather
    # than stale pre-ingestion state. In real async/broker mode this is a
    # no-op refresh (task genuinely hasn't run yet), which is correct: a 202
    # response is expected to reflect "queued", not "already processed".
    await db.refresh(dataset_file)

    return DatasetFileUploadResponse(
        upload=UploadStatusResponse.model_validate(upload),
        dataset_file=DatasetFilePublic.model_validate(dataset_file),
    )


@router.post(
    "/{dataset_id}/uploads/initiate",
    response_model=MultipartUploadInitiateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def initiate_multipart_upload_endpoint(
    dataset_id: uuid.UUID,
    payload: MultipartUploadInitiateRequest,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Starts a direct-to-MinIO multipart upload for a large file — the
    browser PUTs each part's bytes straight to storage using presigned
    URLs from the endpoint below; this backend process never sees the
    file's bytes at all (Phase 1: large uploads bypass FastAPI entirely,
    unlike the single-request /{dataset_id}/files endpoint above, which
    remains unchanged for small files)."""
    site_settings = await settings_service.get_settings(db)
    try:
        upload, total_parts = await dataset_multipart_upload_service.initiate_multipart_upload(
            db,
            dataset_id=dataset_id,
            filename=payload.filename,
            total_size_bytes=payload.total_size_bytes,
            uploaded_by=current_user.id,
            max_upload_size_mb=site_settings.max_upload_size_mb,
        )
    except DatasetMultipartUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return MultipartUploadInitiateResponse(
        upload=UploadStatusResponse.model_validate(upload),
        total_parts=total_parts,
        part_size_bytes=PART_SIZE_BYTES,
    )


@router.post(
    "/uploads/{upload_id}/parts/{part_number}/presign",
    response_model=MultipartUploadPresignPartResponse,
)
async def presign_upload_part_endpoint(
    upload_id: uuid.UUID,
    part_number: int,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    try:
        url = await dataset_multipart_upload_service.presign_part(
            db, upload_id=upload_id, part_number=part_number
        )
    except DatasetMultipartUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return MultipartUploadPresignPartResponse(part_number=part_number, upload_url=url)


@router.post("/uploads/{upload_id}/parts/complete", response_model=UploadStatusResponse)
async def mark_part_uploaded_endpoint(
    upload_id: uuid.UUID,
    payload: MultipartUploadPartCompleteRequest,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Called by the browser right after each part's direct PUT to MinIO
    succeeds — drives the real upload-progress figure (uploaded_bytes)
    from confirmed part completions rather than a client-side guess."""
    try:
        upload = await dataset_multipart_upload_service.mark_part_uploaded(
            db,
            upload_id=upload_id,
            part_number=payload.part_number,
            size_bytes=payload.size_bytes,
        )
    except DatasetMultipartUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return upload


@router.post("/uploads/{upload_id}/complete", response_model=MultipartUploadCompleteResponse)
async def complete_multipart_upload_endpoint(
    upload_id: uuid.UUID,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Finalizes the multipart session (verified against storage's own
    list_parts(), not just what the client claims) and dispatches the same
    ingestion task the small-file path uses — one ingestion pipeline
    regardless of how the raw file arrived in storage."""
    try:
        upload, dataset_file = await dataset_multipart_upload_service.complete_multipart_upload(
            db, upload_id=upload_id
        )
    except DatasetMultipartUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    task = process_dataset_file.apply_async(
        args=[str(dataset_file.id), str(upload.id)],
        soft_time_limit=ingestion_soft_time_limit_seconds(dataset_file.file_size_bytes),
    )
    upload.celery_task_id = task.id
    await db.commit()
    await db.refresh(upload)
    await db.refresh(dataset_file)

    return MultipartUploadCompleteResponse(
        upload=UploadStatusResponse.model_validate(upload),
        dataset_file=DatasetFilePublic.model_validate(dataset_file),
    )


@router.post("/uploads/{upload_id}/cancel", response_model=UploadStatusResponse)
async def cancel_upload_endpoint(
    upload_id: uuid.UUID,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Cancels an upload at any stage — mid-multipart-transfer, queued
    (fully uploaded, not yet processing), or mid-processing. See
    dataset_multipart_upload_service.cancel_upload's docstring for exactly
    what happens at each stage; this endpoint is intentionally a single
    entry point regardless of stage so the frontend never needs to know
    which cleanup path applies."""
    try:
        upload = await dataset_multipart_upload_service.cancel_upload(db, upload_id=upload_id)
    except DatasetMultipartUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return upload


@router.get("/{dataset_id}/uploads", response_model=list[UploadStatusResponse])
async def list_dataset_uploads(
    dataset_id: uuid.UUID,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Upload).where(Upload.dataset_id == dataset_id).order_by(Upload.uploaded_at.desc())
    )
    return result.scalars().all()
