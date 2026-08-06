from datetime import date

import structlog
from sqlalchemy import func, select

from app.core.database import get_sync_db
from app.models.catalog import Dataset, DatasetFile
from app.models.requests import AccessGrant, DatasetRequest, DownloadLog, GrantStatus, RequestStatus
from app.models.stats import DailyStatsSnapshot
from app.models.user import User
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="admin_stats.capture_daily_snapshot", bind=True, max_retries=2)
def capture_daily_snapshot(self) -> dict:
    """Daily scan (Celery beat, Master Plan §3 Phase 8 task 7) — captures
    one row of real platform-wide counts so the admin Overview dashboard
    can compute genuine trend arrows/%-deltas/sparklines instead of
    fabricating them. Unique snapshot_date makes this idempotent: a
    manual re-run on the same day is a safe no-op."""
    today = date.today()

    with get_sync_db() as db:
        existing = db.execute(
            select(DailyStatsSnapshot).where(DailyStatsSnapshot.snapshot_date == today)
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("admin_stats.snapshot_already_captured", snapshot_date=str(today))
            return {"status": "skipped", "reason": "already captured today"}

        total_users = db.execute(select(func.count()).select_from(User)).scalar_one()
        total_datasets = db.execute(select(func.count()).select_from(Dataset)).scalar_one()
        pending_requests = db.execute(
            select(func.count())
            .select_from(DatasetRequest)
            .where(DatasetRequest.status == RequestStatus.PENDING.value)
        ).scalar_one()
        total_downloads = db.execute(select(func.count()).select_from(DownloadLog)).scalar_one()
        active_grants = db.execute(
            select(func.count())
            .select_from(AccessGrant)
            .where(AccessGrant.status == GrantStatus.ACTIVE.value)
        ).scalar_one()
        storage_used_bytes = db.execute(
            select(func.coalesce(func.sum(DatasetFile.file_size_bytes), 0))
        ).scalar_one()

        db.add(
            DailyStatsSnapshot(
                snapshot_date=today,
                total_users=total_users,
                total_datasets=total_datasets,
                pending_requests=pending_requests,
                total_downloads=total_downloads,
                active_grants=active_grants,
                storage_used_bytes=storage_used_bytes,
            )
        )
        db.commit()

    logger.info("admin_stats.snapshot_captured", snapshot_date=str(today))
    return {"status": "complete"}
