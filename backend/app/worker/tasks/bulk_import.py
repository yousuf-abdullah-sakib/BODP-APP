import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_sync_db
from app.models.uploads import Upload, UploadStatus
from app.scripts.bulk_import import BulkImportError, run_bulk_import_transfer
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


async def _run_transfer_with_fresh_engine(**kwargs) -> dict:
    """asyncpg connections are permanently bound to the event loop that
    created them — the app-wide AsyncSessionLocal's connection pool was
    created under FastAPI's (or, in eager-mode tests, pytest-asyncio's)
    event loop, and reusing it from a different thread's fresh loop
    (which is what running inside asyncio.run() on a dedicated worker
    thread means) raises "Future attached to a different loop". A
    dedicated engine, created and disposed entirely within this one
    asyncio.run() call, avoids that: it's never touched by any other
    loop, ever."""
    engine = create_async_engine(str(settings.DATABASE_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        return await run_bulk_import_transfer(session_factory=session_factory, **kwargs)
    finally:
        await engine.dispose()


def _run_async_in_thread(**kwargs) -> dict:
    """Runs the transfer to completion from plain sync Celery-task code,
    safely regardless of whether the calling thread already has a running
    asyncio event loop. asyncio.run() would crash with "cannot be called
    from a running event loop" if this task is ever invoked from inside
    one — a real scenario here since Celery's task_always_eager test mode
    runs tasks inline within pytest-asyncio's own event loop, and this
    task (unlike every other sync-only Celery task in this codebase) needs
    to call async code. A dedicated worker thread always starts with no
    event loop of its own, so asyncio.run() there is always safe."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, _run_transfer_with_fresh_engine(**kwargs)).result()


@celery_app.task(name="bulk_import.run", bind=True, max_retries=0)
def run_admin_bulk_import(self, upload_id: str, dataset_id: str, source_path: str) -> dict:
    """Background counterpart to app.scripts.bulk_import's CLI entrypoint
    (Admin Panel production-readiness follow-up) — runs the (potentially
    very slow, TB-scale) file-to-MinIO transfer off the HTTP request
    thread entirely, so POST /admin/bulk-import returns immediately with a
    real, already-pollable Upload.id rather than blocking the request for
    however long the transfer takes.

    upload_id refers to a real Upload row the router already created
    (status=INITIATED, total_size_bytes known) before dispatching this
    task — run_bulk_import_transfer reuses that row (rather than creating
    a second one) and updates its uploaded_bytes as the stream progresses,
    the same 'create the tracking row first, stream second' shape Phase
    1's multipart upload already established for browser uploads.

    No parallel ingestion logic: this calls the exact same
    run_bulk_import_transfer the CLI's bulk_import(dry_run=False) calls —
    a different ENTRY POINT into one bulk-import implementation, matching
    that module's own "no parallel code path" principle for how bulk
    import itself relates to the ordinary upload endpoint."""

    def _report_progress(bytes_so_far: int) -> None:
        with get_sync_db() as db:
            upload = db.get(Upload, uuid.UUID(upload_id))
            if upload is None or upload.status != UploadStatus.INITIATED.value:
                return
            upload.uploaded_bytes = bytes_so_far
            db.commit()

    try:
        result = _run_async_in_thread(
            dataset_id=uuid.UUID(dataset_id),
            source_path=Path(source_path),
            upload_id=uuid.UUID(upload_id),
            on_bytes_transferred=_report_progress,
        )
        return {"status": "dispatched", **result}
    except BulkImportError as exc:
        logger.warning("bulk_import.task_failed", upload_id=upload_id, reason=str(exc))
        with get_sync_db() as db:
            upload = db.get(Upload, uuid.UUID(upload_id))
            if upload is not None:
                upload.status = UploadStatus.FAILED.value
                upload.error_message = str(exc)
                db.commit()
        return {"status": "failed", "reason": str(exc)}
    except Exception as exc:
        logger.exception("bulk_import.task_unexpected_error", upload_id=upload_id)
        with get_sync_db() as db:
            upload = db.get(Upload, uuid.UUID(upload_id))
            if upload is not None:
                upload.status = UploadStatus.FAILED.value
                upload.error_message = f"Unexpected error during bulk import transfer: {exc}"
                db.commit()
        return {"status": "failed", "reason": str(exc)}
