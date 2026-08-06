import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_sync_db
from app.models.catalog import DatasetFile
from app.models.requests import AccessGrant, ExtractionStatus, GrantStatus, SubsetExtraction
from app.schemas.requests import SearchCriteriaSchema
from app.services.requests_service import validate_scope_within_grant


async def create_extraction(
    db: AsyncSession,
    *,
    grant: AccessGrant,
    requested_scope: SearchCriteriaSchema,
    output_format: str,
) -> SubsetExtraction:
    if grant.status != GrantStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail=f"Grant is {grant.status}, cannot start an extraction")
    if grant.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=409, detail="Grant has expired, cannot start an extraction")

    validate_scope_within_grant(requested_scope, grant.scope)

    extraction = SubsetExtraction(
        grant_id=grant.id,
        requested_scope=requested_scope.model_dump(exclude_none=True),
        format=output_format,
        status=ExtractionStatus.QUEUED.value,
    )
    db.add(extraction)
    await db.commit()
    await db.refresh(extraction)

    total_size = await db.scalar(
        select(func.coalesce(func.sum(DatasetFile.file_size_bytes), 0))
        .where(DatasetFile.dataset_id == grant.dataset_id)
    )
    threshold_bytes = settings.EXTRACTION_SYNC_THRESHOLD_MB * 1024 * 1024

    if total_size and total_size <= threshold_bytes:
        # Small-file fast path (Master Plan §3 Phase 5 task 5) — run
        # in-process on a sync session rather than dispatching to Celery,
        # so the caller gets back an already-COMPLETE row synchronously.
        from app.worker.tasks.extraction import process_extraction

        with get_sync_db() as sync_db:
            process_extraction(sync_db, str(extraction.id))
        await db.refresh(extraction)
    else:
        from app.worker.tasks.extraction import run_extraction

        task = run_extraction.delay(str(extraction.id))
        extraction.celery_task_id = task.id
        await db.commit()
        await db.refresh(extraction)

    return extraction


async def get_extraction_for_user(
    db: AsyncSession, extraction_id: uuid.UUID, user_id: uuid.UUID
) -> SubsetExtraction:
    result = await db.execute(
        select(SubsetExtraction)
        .join(AccessGrant, SubsetExtraction.grant_id == AccessGrant.id)
        .where(SubsetExtraction.id == extraction_id, AccessGrant.user_id == user_id)
    )
    extraction = result.scalar_one_or_none()
    if extraction is None:
        raise HTTPException(status_code=404, detail="Extraction not found")
    return extraction
