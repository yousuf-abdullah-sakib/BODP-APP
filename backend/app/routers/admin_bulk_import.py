from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import require_permission
from app.models.uploads import Upload, UploadStatus
from app.schemas.admin_bulk_import import (
    BulkImportStartRequest,
    BulkImportStartResponse,
    BulkImportValidateRequest,
    BulkImportValidateResponse,
)
from app.schemas.datasets import UploadStatusResponse
from app.scripts.bulk_import import BulkImportError, validate_bulk_import
from app.worker.tasks.bulk_import import run_admin_bulk_import

router = APIRouter(prefix="/admin/bulk-import", tags=["admin-bulk-import"])

# Same admin capability as any other dataset-file ingestion entry point —
# bulk import is functionally "add data to a dataset," just sourced from a
# server-local/NAS path instead of a browser upload (see
# app/scripts/bulk_import.py's module docstring: "a different ENTRY POINT
# into one ingestion pipeline, not a second pipeline").
_PERMISSION = "Edit Datasets"


@router.post("/validate", response_model=BulkImportValidateResponse)
async def validate(
    payload: BulkImportValidateRequest,
    current_user=Depends(require_permission(_PERMISSION)),
):
    """Pre-flight check only — validates the server-side path exists and
    is readable/importable, returns what would happen, writes nothing.
    Mirrors the CLI's dry_run=True behavior exactly (same underlying
    validate_bulk_import call)."""
    try:
        plan = await validate_bulk_import(
            dataset_id=payload.dataset_id, source_path=Path(payload.source_path)
        )
    except BulkImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BulkImportValidateResponse(**plan)


@router.post("", response_model=BulkImportStartResponse, status_code=202)
async def start(
    payload: BulkImportStartRequest,
    current_user=Depends(require_permission(_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    """Starts an asynchronous bulk import — validates synchronously (fast,
    no file I/O beyond a stat), then dispatches the actual (potentially
    very slow) file-to-MinIO transfer as a background Celery task and
    returns immediately with a real, already-pollable Upload.id.

    The browser/HTTP request never touches the file's bytes at all — the
    source file is read directly from the server/NAS path by the Celery
    worker container, which must have that path mounted/reachable (same
    requirement the CLI script already has)."""
    source_path = Path(payload.source_path)
    try:
        plan = await validate_bulk_import(dataset_id=payload.dataset_id, source_path=source_path)
    except BulkImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Real, immediately-pollable placeholder row — created here (fast)
    # rather than inside the background task, so the response below can
    # return a genuine Upload.id the frontend can poll right away, before
    # the (possibly hours-long) transfer even starts. INITIATED mirrors
    # exactly how Phase 1's multipart upload uses that same status for
    # "session started, bytes not yet fully in storage."
    upload = Upload(
        dataset_id=payload.dataset_id,
        file_name=plan["filename"],
        status=UploadStatus.INITIATED.value,
        total_size_bytes=plan["size_bytes"],
        uploaded_bytes=0,
        uploaded_by=current_user.id,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    task = run_admin_bulk_import.apply_async(
        args=[str(upload.id), str(payload.dataset_id), str(source_path)]
    )
    upload.celery_task_id = task.id
    await db.commit()
    await db.refresh(upload)

    return BulkImportStartResponse(upload=UploadStatusResponse.model_validate(upload))
