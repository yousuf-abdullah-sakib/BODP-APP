import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditActionType
from app.models.catalog import Dataset, DatasetFile, DatasetRecord, StorageKind
from app.models.uploads import (
    QualityIssue,
    QualityIssueSeverity,
    QualityIssueStatus,
    QualityIssueType,
)
from app.models.user import User
from app.services.admin_notify_service import notify_admins
from app.services.audit_service import write_audit_log
from app.services.query_concurrency import bounded_to_thread

# A material gap between a dataset's declared record_count and its actual
# DatasetRecord row count flags "Missing Values" — DatasetRecord.value is
# NOT NULL at the DB level, so there's no per-row null to detect directly;
# this count-gap comparison is the practical equivalent for this schema.
_MISSING_VALUES_THRESHOLD = 0.95
_OUTLIER_STDDEV_MULTIPLIER = 3


async def _parquet_files(db: AsyncSession) -> list[DatasetFile]:
    """PLAN.md Phase 5: every PARQUET-backed DatasetFile across all
    datasets — the input every detector below scans in addition to its
    existing DatasetRecord query, closing the QC blind spot new tabular
    ingestion would otherwise silently create (see this module's own
    docstring addition / PLAN.md's "Admin QC scanning" item)."""
    result = await db.execute(
        select(DatasetFile).where(DatasetFile.storage_kind == StorageKind.PARQUET.value)
    )
    return list(result.scalars().all())


async def _has_open_issue(db: AsyncSession, dataset_id: uuid.UUID, issue_type: str) -> bool:
    result = await db.execute(
        select(func.count())
        .select_from(QualityIssue)
        .where(
            QualityIssue.dataset_id == dataset_id,
            QualityIssue.issue_type == issue_type,
            QualityIssue.status == QualityIssueStatus.OPEN.value,
        )
    )
    return result.scalar_one() > 0


async def _detect_duplicates(db: AsyncSession) -> list[QualityIssue]:
    # Phase 2: time/station_id are both nullable (not every dataset has a
    # time dimension or station attribution). Postgres GROUP BY treats
    # NULL = NULL as equal, so without this filter every timeless,
    # station-less row of the same parameter in a dataset would collapse
    # into one group and falsely flag as "duplicates" the moment there are
    # 2+ such rows — which is normal for e.g. a static spatial grid where
    # lat/lon (not time/station) is what actually distinguishes rows.
    # Excluding rows with neither dimension set means this check only ever
    # runs against records where (time, station_id) genuinely identifies
    # a real observation slot, matching what it was designed to catch.
    result = await db.execute(
        select(DatasetRecord.dataset_id, func.count())
        .where(
            (DatasetRecord.time.is_not(None)) | (DatasetRecord.station_id.is_not(None))
        )
        .group_by(
            DatasetRecord.dataset_id,
            DatasetRecord.time,
            DatasetRecord.station_id,
            DatasetRecord.parameter,
        )
        .having(func.count() > 1)
    )
    dataset_ids_with_dupes = {row[0] for row in result.all()}

    # PLAN.md Phase 5: same check against every PARQUET-backed file via
    # DuckDB — additive, never replacing the DatasetRecord query above
    # (a dataset can have files of both kinds, per the non-destructive
    # migration requirement).
    from app.services import tabular_query_service

    for file in await _parquet_files(db):
        has_dupes, _detail = await bounded_to_thread(tabular_query_service.detect_duplicates, file)
        if has_dupes:
            dataset_ids_with_dupes.add(file.dataset_id)

    issues = []
    for dataset_id in dataset_ids_with_dupes:
        if await _has_open_issue(db, dataset_id, QualityIssueType.DUPLICATE_RECORDS.value):
            continue
        issues.append(
            QualityIssue(
                dataset_id=dataset_id,
                issue_type=QualityIssueType.DUPLICATE_RECORDS.value,
                severity=QualityIssueSeverity.MEDIUM.value,
                status=QualityIssueStatus.OPEN.value,
                detail="Duplicate records found for one or more (time, station, parameter) combinations.",
            )
        )
    return issues


async def _detect_outliers(db: AsyncSession) -> list[QualityIssue]:
    stats_result = await db.execute(
        select(
            DatasetRecord.dataset_id,
            DatasetRecord.parameter,
            func.avg(DatasetRecord.value),
            func.stddev(DatasetRecord.value),
        ).group_by(DatasetRecord.dataset_id, DatasetRecord.parameter)
    )
    stats = stats_result.all()

    issues = []
    flagged_datasets: dict[uuid.UUID, int] = {}
    for dataset_id, parameter, avg_value, stddev_value in stats:
        if avg_value is None or stddev_value is None or stddev_value <= 0:
            continue
        count_result = await db.execute(
            select(func.count())
            .select_from(DatasetRecord)
            .where(
                DatasetRecord.dataset_id == dataset_id,
                DatasetRecord.parameter == parameter,
                func.abs(DatasetRecord.value - avg_value) > _OUTLIER_STDDEV_MULTIPLIER * stddev_value,
            )
        )
        outlier_count = count_result.scalar_one()
        if outlier_count > 0:
            flagged_datasets[dataset_id] = flagged_datasets.get(dataset_id, 0) + outlier_count

    # PLAN.md Phase 5: same 3-sigma check against every PARQUET-backed
    # file via DuckDB, summed additively into the same flagged_datasets
    # totals the DatasetRecord query above populates.
    from app.services import tabular_query_service

    for file in await _parquet_files(db):
        has_outliers, _detail, outlier_count = await bounded_to_thread(
            tabular_query_service.detect_outliers, file, stddev_multiplier=_OUTLIER_STDDEV_MULTIPLIER
        )
        if has_outliers:
            flagged_datasets[file.dataset_id] = flagged_datasets.get(file.dataset_id, 0) + outlier_count

    for dataset_id, total_outliers in flagged_datasets.items():
        if await _has_open_issue(db, dataset_id, QualityIssueType.OUTLIERS.value):
            continue
        severity = QualityIssueSeverity.LOW.value if total_outliers < 10 else QualityIssueSeverity.MEDIUM.value
        issues.append(
            QualityIssue(
                dataset_id=dataset_id,
                issue_type=QualityIssueType.OUTLIERS.value,
                severity=severity,
                status=QualityIssueStatus.OPEN.value,
                detail=f"{total_outliers} value(s) more than {_OUTLIER_STDDEV_MULTIPLIER} standard deviations from the mean.",
            )
        )
    return issues


async def _detect_missing_values(db: AsyncSession) -> list[QualityIssue]:
    result = await db.execute(select(Dataset.id, Dataset.record_count).where(Dataset.record_count > 0))
    datasets = result.all()

    # PLAN.md Phase 5: group PARQUET-backed files by dataset once, up
    # front, so each dataset's loop iteration below can add its own
    # files' actual row counts to the DatasetRecord count — a dataset's
    # "actual" total must reflect BOTH storage kinds it may have.
    from app.services import tabular_query_service
    from app.services.catalog_service import RecordsFilter

    parquet_files_by_dataset: dict[uuid.UUID, list[DatasetFile]] = {}
    for file in await _parquet_files(db):
        parquet_files_by_dataset.setdefault(file.dataset_id, []).append(file)

    issues = []
    for dataset_id, declared_count in datasets:
        actual_result = await db.execute(
            select(func.count()).select_from(DatasetRecord).where(DatasetRecord.dataset_id == dataset_id)
        )
        actual_count = actual_result.scalar_one()
        for file in parquet_files_by_dataset.get(dataset_id, []):
            _matching, file_total = await bounded_to_thread(
                tabular_query_service.get_matching_record_counts, file, RecordsFilter()
            )
            actual_count += file_total
        if actual_count < declared_count * _MISSING_VALUES_THRESHOLD:
            if await _has_open_issue(db, dataset_id, QualityIssueType.MISSING_VALUES.value):
                continue
            issues.append(
                QualityIssue(
                    dataset_id=dataset_id,
                    issue_type=QualityIssueType.MISSING_VALUES.value,
                    severity=QualityIssueSeverity.MEDIUM.value,
                    status=QualityIssueStatus.OPEN.value,
                    detail=f"Expected ~{declared_count} records, found {actual_count}.",
                )
            )
    return issues


async def run_qc_scan(db: AsyncSession, *, actor: User, ip_address: str | None) -> list[QualityIssue]:
    new_issues: list[QualityIssue] = []
    new_issues += await _detect_duplicates(db)
    new_issues += await _detect_outliers(db)
    new_issues += await _detect_missing_values(db)

    for issue in new_issues:
        db.add(issue)

    if new_issues:
        await notify_admins(
            db,
            type="warning",
            title="New data quality issues detected",
            description=f"QC scan found {len(new_issues)} new issue(s) requiring review.",
        )

    await write_audit_log(
        db,
        actor=actor,
        action=f"Ran QC scan — {len(new_issues)} issue(s) found",
        action_type=AuditActionType.DATASET,
        target="all datasets",
        ip_address=ip_address,
    )
    await db.commit()
    for issue in new_issues:
        await db.refresh(issue)
    return new_issues


async def list_quality_issues(db: AsyncSession, *, status_filter: str | None = None) -> list[QualityIssue]:
    query = (
        select(QualityIssue)
        .options(selectinload(QualityIssue.dataset))
        .order_by(QualityIssue.detected_at.desc())
    )
    if status_filter:
        query = query.where(QualityIssue.status == status_filter)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_quality_issue(db: AsyncSession, issue_id: uuid.UUID) -> QualityIssue:
    result = await db.execute(
        select(QualityIssue).options(selectinload(QualityIssue.dataset)).where(QualityIssue.id == issue_id)
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Quality issue not found")
    return issue


async def update_issue_status(
    db: AsyncSession, *, issue: QualityIssue, status: str, actor: User, ip_address: str | None
) -> QualityIssue:
    issue.status = status
    await write_audit_log(
        db,
        actor=actor,
        action=f"Marked quality issue as {status}",
        action_type=AuditActionType.DATASET,
        target=f"{issue.issue_type} — {issue.dataset.title if issue.dataset else issue.dataset_id}",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(issue)
    return issue
