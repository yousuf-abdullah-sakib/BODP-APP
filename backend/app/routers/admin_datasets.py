import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.catalog import Dataset, DatasetStatus
from app.models.uploads import Upload
from app.models.user import User
from app.schemas.admin_datasets import DatasetCreateMinimal, DatasetMinimalPublic
from app.schemas.datasets import DatasetFilePublic, DatasetFileUploadResponse, UploadStatusResponse
from app.services.dataset_file_service import DatasetFileUploadError, upload_dataset_file
from app.worker.tasks.ingestion import process_dataset_file

router = APIRouter(prefix="/admin/datasets", tags=["admin-datasets"])


def _generate_code(sequence_hint: int) -> str:
    return f"BD-{sequence_hint:04d}"


@router.post("", response_model=DatasetMinimalPublic, status_code=status.HTTP_201_CREATED)
async def create_dataset_shell(
    payload: DatasetCreateMinimal,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Minimal dataset-shell creation — see Phase 8 for the full admin
    Dataset create/edit form (category, platforms, processing levels, etc.).
    This exists so Phase 2's file-upload pipeline has a real FK target."""
    code = payload.code
    if not code:
        count_result = await db.execute(select(Dataset))
        existing_count = len(count_result.scalars().all())
        code = _generate_code(existing_count + 1)

    dataset = Dataset(
        code=code,
        title=payload.title,
        description=payload.description,
        status=DatasetStatus.DRAFT.value,
        created_by=current_user.id,
    )
    db.add(dataset)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A dataset with this code already exists") from exc
    await db.refresh(dataset)
    return dataset


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

    try:
        upload, dataset_file = await upload_dataset_file(
            db,
            dataset_id=dataset_id,
            filename=file.filename,
            file_stream=file,
            uploaded_by=current_user.id,
        )
    except DatasetFileUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    task = process_dataset_file.delay(str(dataset_file.id), str(upload.id))
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


@router.get("/uploads/{upload_id}", response_model=UploadStatusResponse)
async def get_upload_status(
    upload_id: uuid.UUID,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    """Real status polling endpoint (Master Plan §3 Phase 2 deliverable),
    replacing the prototype's hardcoded 2500ms setTimeout — the frontend's
    Data Upload section polls this to reflect actual Celery task state."""
    result = await db.execute(select(Upload).where(Upload.id == upload_id))
    upload = result.scalar_one_or_none()
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
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
