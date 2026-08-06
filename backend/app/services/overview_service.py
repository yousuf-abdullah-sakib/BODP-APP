import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLogEntry
from app.models.catalog import Dataset, DatasetView
from app.models.requests import AccessGrant, DatasetRequest, DownloadLog, RequestStatus, SubsetExtraction
from app.models.user import User
from app.schemas.me import ActivityItem, OverviewResponse

_ACTIVITY_LIMIT = 10


async def get_overview(db: AsyncSession, user: User) -> OverviewResponse:
    pending = await db.scalar(
        select(func.count()).select_from(DatasetRequest).where(
            DatasetRequest.user_id == user.id, DatasetRequest.status == RequestStatus.PENDING.value
        )
    )
    approved = await db.scalar(
        select(func.count()).select_from(DatasetRequest).where(
            DatasetRequest.user_id == user.id, DatasetRequest.status == RequestStatus.APPROVED.value
        )
    )
    total_downloads = await db.scalar(
        select(func.count()).select_from(DownloadLog).where(DownloadLog.user_id == user.id)
    )
    datasets_viewed = await db.scalar(
        select(func.count(func.distinct(DatasetView.dataset_id))).where(DatasetView.user_id == user.id)
    )
    # Real bytes actually extracted across the user's grants — no fake
    # quota/percentage, since no per-user storage limit concept exists.
    total_extracted_bytes = await _sum_extracted_bytes(db, user.id)

    recent_activity = await _recent_activity(db, user.id)

    return OverviewResponse(
        pending_requests=pending or 0,
        approved_requests=approved or 0,
        datasets_granted=user.datasets_granted or 0,
        total_downloads=total_downloads or 0,
        datasets_viewed=datasets_viewed or 0,
        total_extracted_bytes=total_extracted_bytes or 0,
        member_since=user.created_at,
        recent_activity=recent_activity,
    )


async def _sum_extracted_bytes(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.coalesce(func.sum(SubsetExtraction.output_size_bytes), 0))
        .select_from(SubsetExtraction)
        .join(AccessGrant, SubsetExtraction.grant_id == AccessGrant.id)
        .where(AccessGrant.user_id == user_id)
    )
    return result.scalar_one()


async def _recent_activity(db: AsyncSession, user_id: uuid.UUID) -> list[ActivityItem]:
    items: list[ActivityItem] = []

    requests_result = await db.execute(
        select(DatasetRequest, Dataset.title)
        .join(Dataset, DatasetRequest.dataset_id == Dataset.id)
        .where(DatasetRequest.user_id == user_id)
        .order_by(DatasetRequest.submitted_at.desc())
        .limit(_ACTIVITY_LIMIT)
    )
    for req, dataset_title in requests_result.all():
        items.append(
            ActivityItem(
                type="request_submitted",
                description=f"Requested access to \"{dataset_title}\"",
                occurred_at=req.submitted_at,
            )
        )
        if req.status != RequestStatus.PENDING.value and req.reviewed_at is not None:
            verb = "approved" if req.status == RequestStatus.APPROVED.value else "rejected"
            items.append(
                ActivityItem(
                    type=f"request_{verb}",
                    description=f"Your request for \"{dataset_title}\" was {verb}",
                    occurred_at=req.reviewed_at,
                )
            )

    downloads_result = await db.execute(
        select(DownloadLog, Dataset.title)
        .join(AccessGrant, DownloadLog.grant_id == AccessGrant.id)
        .join(Dataset, AccessGrant.dataset_id == Dataset.id)
        .where(DownloadLog.user_id == user_id)
        .order_by(DownloadLog.downloaded_at.desc())
        .limit(_ACTIVITY_LIMIT)
    )
    for log, dataset_title in downloads_result.all():
        items.append(
            ActivityItem(
                type="download",
                description=f"Downloaded a subset of \"{dataset_title}\"",
                occurred_at=log.downloaded_at,
            )
        )

    audit_result = await db.execute(
        select(AuditLogEntry)
        .where(AuditLogEntry.actor_id == user_id)
        .order_by(AuditLogEntry.created_at.desc())
        .limit(_ACTIVITY_LIMIT)
    )
    for entry in audit_result.scalars().all():
        items.append(
            ActivityItem(
                type=entry.action_type,
                description=entry.action,
                occurred_at=entry.created_at,
            )
        )

    items.sort(key=lambda i: i.occurred_at, reverse=True)
    return items[:_ACTIVITY_LIMIT]
