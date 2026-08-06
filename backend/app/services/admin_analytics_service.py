from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Dataset, DatasetCategory
from app.models.requests import AccessGrant, DatasetRequest, DownloadLog, RequestStatus
from app.models.user import User
from app.schemas.admin_analytics import (
    AnalyticsResponse,
    CategoryCount,
    DailyCount,
    TopDataset,
)


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=timezone.utc)
    end = datetime.combine(d, time.max, tzinfo=timezone.utc)
    return start, end


async def _daily_counts(db: AsyncSession, model, date_column, *, date_from: datetime, date_to: datetime) -> list[DailyCount]:
    day_expr = func.date_trunc("day", date_column)
    result = await db.execute(
        select(day_expr, func.count())
        .select_from(model)
        .where(date_column >= date_from, date_column <= date_to)
        .group_by(day_expr)
        .order_by(day_expr)
    )
    return [DailyCount(date=row[0].date(), count=row[1]) for row in result.all()]


async def get_analytics(db: AsyncSession, *, date_from: datetime, date_to: datetime) -> AnalyticsResponse:
    requests_submitted = await db.scalar(
        select(func.count())
        .select_from(DatasetRequest)
        .where(DatasetRequest.submitted_at >= date_from, DatasetRequest.submitted_at <= date_to)
    )
    approved_in_range = await db.scalar(
        select(func.count())
        .select_from(DatasetRequest)
        .where(
            DatasetRequest.submitted_at >= date_from,
            DatasetRequest.submitted_at <= date_to,
            DatasetRequest.status == RequestStatus.APPROVED.value,
        )
    )
    approval_rate_pct = round((approved_in_range or 0) / requests_submitted * 100, 1) if requests_submitted else 0.0

    total_downloads = await db.scalar(
        select(func.count())
        .select_from(DownloadLog)
        .where(DownloadLog.downloaded_at >= date_from, DownloadLog.downloaded_at <= date_to)
    )
    new_researchers = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.created_at >= date_from, User.created_at <= date_to)
    )

    requests_over_time = await _daily_counts(
        db, DatasetRequest, DatasetRequest.submitted_at, date_from=date_from, date_to=date_to
    )
    downloads_over_time = await _daily_counts(
        db, DownloadLog, DownloadLog.downloaded_at, date_from=date_from, date_to=date_to
    )
    new_users_over_time = await _daily_counts(
        db, User, User.created_at, date_from=date_from, date_to=date_to
    )

    status_result = await db.execute(
        select(DatasetRequest.status, func.count())
        .select_from(DatasetRequest)
        .where(DatasetRequest.submitted_at >= date_from, DatasetRequest.submitted_at <= date_to)
        .group_by(DatasetRequest.status)
    )
    requests_by_status = {row[0]: row[1] for row in status_result.all()}

    category_result = await db.execute(
        select(DatasetCategory.name, func.count(DatasetRequest.id))
        .select_from(DatasetRequest)
        .join(Dataset, DatasetRequest.dataset_id == Dataset.id)
        .join(DatasetCategory, Dataset.category_id == DatasetCategory.id)
        .where(DatasetRequest.submitted_at >= date_from, DatasetRequest.submitted_at <= date_to)
        .group_by(DatasetCategory.name)
        .order_by(func.count(DatasetRequest.id).desc())
    )
    requests_by_category = [CategoryCount(category=row[0], count=row[1]) for row in category_result.all()]

    top_result = await db.execute(
        select(Dataset.title, func.count(AccessGrant.id))
        .select_from(AccessGrant)
        .join(Dataset, AccessGrant.dataset_id == Dataset.id)
        .group_by(Dataset.title)
        .order_by(func.count(AccessGrant.id).desc())
        .limit(5)
    )
    top_datasets_by_grants = [TopDataset(title=row[0], count=row[1]) for row in top_result.all()]

    return AnalyticsResponse(
        requests_submitted=requests_submitted or 0,
        approval_rate_pct=approval_rate_pct,
        total_downloads=total_downloads or 0,
        new_researchers=new_researchers or 0,
        requests_over_time=requests_over_time,
        downloads_over_time=downloads_over_time,
        new_users_over_time=new_users_over_time,
        requests_by_status=requests_by_status,
        requests_by_category=requests_by_category,
        top_datasets_by_grants=top_datasets_by_grants,
    )
