import csv
import io
import uuid

import structlog
from sqlalchemy import func, select

from app.core.database import get_sync_db
from app.models.admin import Report
from app.models.audit import AuditActionType, AuditLogEntry
from app.models.catalog import Dataset, DatasetCategory, DatasetFile
from app.models.requests import AccessGrant, DatasetRequest, DownloadLog, RequestStatus
from app.models.user import User
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _rows_to_csv(header: list[str], rows: list[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _generate_usage_summary(db) -> bytes:
    # Each date_trunc expression is built ONCE and reused across
    # select/group_by/order_by. Calling func.date_trunc(...) again per
    # clause binds "day" as a fresh, separately-numbered parameter each
    # time (date_trunc_2 vs date_trunc_3 vs ...) — textually identical SQL,
    # but Postgres treats them as different expressions and rejects the
    # query with "column must appear in the GROUP BY clause or be used in
    # an aggregate function", since it can't prove the SELECT and GROUP BY
    # expressions match. Reusing one expression object (as
    # admin_analytics_service._daily_counts already does) avoids this.
    submitted_day = func.date_trunc("day", DatasetRequest.submitted_at)
    requests_by_day = db.execute(
        select(submitted_day, func.count())
        .select_from(DatasetRequest)
        .group_by(submitted_day)
        .order_by(submitted_day)
    ).all()
    reviewed_day = func.date_trunc("day", DatasetRequest.reviewed_at)
    approvals_by_day = dict(
        db.execute(
            select(reviewed_day, func.count())
            .select_from(DatasetRequest)
            .where(DatasetRequest.status == RequestStatus.APPROVED.value)
            .group_by(reviewed_day)
        ).all()
    )
    downloaded_day = func.date_trunc("day", DownloadLog.downloaded_at)
    downloads_by_day = dict(
        db.execute(
            select(downloaded_day, func.count())
            .select_from(DownloadLog)
            .group_by(downloaded_day)
        ).all()
    )

    rows = [
        [day.date().isoformat(), count, approvals_by_day.get(day, 0), downloads_by_day.get(day, 0)]
        for day, count in requests_by_day
    ]
    return _rows_to_csv(["Date", "Requests Submitted", "Requests Approved", "Downloads"], rows)


def _generate_dataset_inventory(db) -> bytes:
    results = db.execute(
        select(Dataset, DatasetCategory.name)
        .outerjoin(DatasetCategory, Dataset.category_id == DatasetCategory.id)
        .order_by(Dataset.code)
    ).all()

    rows = []
    for dataset, category_name in results:
        file_count = db.execute(
            select(func.count()).select_from(DatasetFile).where(DatasetFile.dataset_id == dataset.id)
        ).scalar_one()
        total_size = db.execute(
            select(func.coalesce(func.sum(DatasetFile.file_size_bytes), 0))
            .select_from(DatasetFile)
            .where(DatasetFile.dataset_id == dataset.id)
        ).scalar_one()
        rows.append(
            [
                dataset.code,
                dataset.title,
                category_name or "Uncategorized",
                dataset.status,
                dataset.record_count,
                file_count,
                total_size,
                dataset.created_at.date().isoformat(),
            ]
        )
    return _rows_to_csv(
        ["Code", "Title", "Category", "Status", "Record Count", "File Count", "Total Size (bytes)", "Created"],
        rows,
    )


def _generate_user_activity(db) -> bytes:
    users = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()

    rows = []
    for user in users:
        last_login = db.execute(
            select(func.max(AuditLogEntry.created_at)).where(
                AuditLogEntry.actor_id == user.id, AuditLogEntry.action_type == AuditActionType.LOGIN.value
            )
        ).scalar_one()
        rows.append(
            [
                user.email,
                user.full_name,
                user.institution or "",
                user.role,
                user.status,
                user.datasets_granted or 0,
                user.created_at.date().isoformat(),
                last_login.isoformat() if last_login else "",
            ]
        )
    return _rows_to_csv(
        ["Email", "Full Name", "Institution", "Role", "Status", "Datasets Granted", "Joined", "Last Login"],
        rows,
    )


def _generate_access_grants(db) -> bytes:
    results = db.execute(
        select(AccessGrant, User.email, Dataset.title, Dataset.code)
        .join(User, AccessGrant.user_id == User.id)
        .join(Dataset, AccessGrant.dataset_id == Dataset.id)
        .order_by(AccessGrant.granted_at.desc())
    ).all()

    rows = [
        [
            user_email,
            dataset_code,
            dataset_title,
            grant.status,
            grant.granted_at.date().isoformat(),
            grant.expires_at.date().isoformat(),
        ]
        for grant, user_email, dataset_title, dataset_code in results
    ]
    return _rows_to_csv(["User Email", "Dataset Code", "Dataset Title", "Status", "Granted", "Expires"], rows)


_GENERATORS = {
    "usage_summary": _generate_usage_summary,
    "dataset_inventory": _generate_dataset_inventory,
    "user_activity": _generate_user_activity,
    "access_grants": _generate_access_grants,
}


@celery_app.task(name="reports.generate_report", bind=True, max_retries=2)
def generate_report(self, report_id: str) -> dict:
    with get_sync_db() as db:
        report = db.get(Report, uuid.UUID(report_id))
        if report is None:
            logger.error("reports.report_not_found", report_id=report_id)
            return {"status": "failed", "reason": "report not found"}

        generator = _GENERATORS.get(report.type)
        if generator is None:
            logger.error("reports.unknown_type", report_type=report.type)
            return {"status": "failed", "reason": f"unknown report type {report.type!r}"}

        csv_bytes = generator(db)

        backend_name = "vps_minio"
        bucket = default_bucket_for(backend_name)
        storage = get_storage_backend(backend_name)
        storage.ensure_bucket(bucket)
        key = f"reports/{report.id}/{report.type}.csv"
        storage.put(bucket, key, io.BytesIO(csv_bytes), content_type="text/csv")

        report.output_storage_key = key
        db.commit()

    logger.info("reports.generated", report_id=report_id, report_type=report.type)
    return {"status": "complete"}
