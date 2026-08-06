from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.models.audit import AuditLogEntry
from app.models.catalog import Dataset, DatasetCategory, DatasetFile
from app.models.requests import AccessGrant, DatasetRequest, DownloadLog, GrantStatus, RequestStatus
from app.models.stats import DailyStatsSnapshot
from app.models.user import User, UserStatus
from app.schemas.admin_overview import (
    ActivityEntry,
    AdminOverviewResponse,
    RequestPreview,
    StatCardValue,
    StorageUsageSchema,
    SystemHealthSchema,
)
from app.services import requests_service, settings_service
from app.services.storage.registry import get_storage_backend

_SNAPSHOT_LOOKBACK = 8
_ACTIVITY_LIMIT = 10


async def _stat_card(db: AsyncSession, current: int, field: str | None) -> StatCardValue:
    # field=None for stats the snapshot table doesn't track individually
    # (e.g. active_users, which isn't one of the six captured columns) —
    # report the real current value with no trend data rather than
    # borrowing another field's history and mislabeling it.
    if field is None:
        return StatCardValue(current=current, previous=None, delta_pct=None, sparkline=[])

    result = await db.execute(
        select(DailyStatsSnapshot)
        .order_by(DailyStatsSnapshot.snapshot_date.desc())
        .limit(_SNAPSHOT_LOOKBACK)
    )
    snapshots = list(result.scalars().all())
    snapshots.reverse()  # oldest -> newest

    sparkline = [getattr(s, field) for s in snapshots]

    if len(snapshots) < 1:
        return StatCardValue(current=current, previous=None, delta_pct=None, sparkline=sparkline)

    # Most recent captured day vs. today's live value — snapshots is
    # ordered oldest -> newest, so [-1] is the latest snapshot.
    previous = getattr(snapshots[-1], field)
    if previous == 0:
        delta_pct = None
    else:
        delta_pct = round((current - previous) / previous * 100, 1)
    return StatCardValue(current=current, previous=previous, delta_pct=delta_pct, sparkline=sparkline)


async def _category_distribution(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(
        select(DatasetCategory.name, func.count(Dataset.id))
        .select_from(Dataset)
        .join(DatasetCategory, Dataset.category_id == DatasetCategory.id)
        .group_by(DatasetCategory.name)
    )
    distribution = {name: count for name, count in result.all()}

    uncategorized = await db.scalar(
        select(func.count()).select_from(Dataset).where(Dataset.category_id.is_(None))
    )
    if uncategorized:
        distribution["Uncategorized"] = uncategorized
    return distribution


async def _top_downloaded(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Dataset.title, func.count(DownloadLog.id))
        .select_from(DownloadLog)
        .join(AccessGrant, DownloadLog.grant_id == AccessGrant.id)
        .join(Dataset, AccessGrant.dataset_id == Dataset.id)
        .group_by(Dataset.title)
        .order_by(func.count(DownloadLog.id).desc())
        .limit(5)
    )
    return [{"dataset_title": title, "count": count} for title, count in result.all()]


async def _recent_requests(db: AsyncSession) -> list[RequestPreview]:
    result = await db.execute(
        select(DatasetRequest, Dataset.title, User.full_name)
        .join(Dataset, DatasetRequest.dataset_id == Dataset.id)
        .join(User, DatasetRequest.user_id == User.id)
        .where(DatasetRequest.status == RequestStatus.PENDING.value)
        .order_by(DatasetRequest.submitted_at.desc())
        .limit(4)
    )
    return [
        RequestPreview(
            id=str(req.id), dataset_title=title, requester_name=full_name, submitted_at=req.submitted_at
        )
        for req, title, full_name in result.all()
    ]


async def _recent_activity(db: AsyncSession) -> list[ActivityEntry]:
    result = await db.execute(
        select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc()).limit(_ACTIVITY_LIMIT)
    )
    return [
        ActivityEntry(description=entry.action, occurred_at=entry.created_at)
        for entry in result.scalars().all()
    ]


async def _check_system_health(db: AsyncSession) -> SystemHealthSchema:
    db_ok = True
    try:
        await db.execute(select(1))
    except Exception:
        db_ok = False

    redis_ok = True
    try:
        await get_redis().ping()
    except Exception:
        redis_ok = False

    storage_ok = True
    try:
        from app.core.config import settings as app_settings

        get_storage_backend("vps_minio").ensure_bucket(app_settings.STORAGE_VPS_BUCKET)
    except Exception:
        storage_ok = False

    return SystemHealthSchema(
        database=db_ok, redis=redis_ok, storage=storage_ok, checked_at=datetime.now(UTC)
    )


async def get_admin_overview(db: AsyncSession) -> AdminOverviewResponse:
    total_users = await db.scalar(select(func.count()).select_from(User))
    active_users = await db.scalar(
        select(func.count()).select_from(User).where(User.status == UserStatus.ACTIVE.value)
    )
    total_datasets = await db.scalar(select(func.count()).select_from(Dataset))
    downloads = await db.scalar(select(func.count()).select_from(DownloadLog))
    pending_requests = await requests_service.count_pending_requests(db)
    storage_used_bytes = await db.scalar(
        select(func.coalesce(func.sum(DatasetFile.file_size_bytes), 0))
    )

    site_settings = await settings_service.get_settings(db)
    capacity = site_settings.storage_capacity_bytes
    percent_used = round(storage_used_bytes / capacity * 100, 1) if capacity else None

    return AdminOverviewResponse(
        total_users=await _stat_card(db, total_users or 0, "total_users"),
        active_users=await _stat_card(db, active_users or 0, None),
        total_datasets=await _stat_card(db, total_datasets or 0, "total_datasets"),
        downloads=await _stat_card(db, downloads or 0, "total_downloads"),
        pending_requests=await _stat_card(db, pending_requests or 0, "pending_requests"),
        recent_requests=await _recent_requests(db),
        recent_activity=await _recent_activity(db),
        category_distribution=await _category_distribution(db),
        storage=StorageUsageSchema(
            used_bytes=storage_used_bytes or 0, capacity_bytes=capacity, percent_used=percent_used
        ),
        top_downloaded=await _top_downloaded(db),
        system_health=await _check_system_health(db),
    )
