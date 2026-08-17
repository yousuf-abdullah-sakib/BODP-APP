from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select

from app.core.database import get_sync_db
from app.models.catalog import Dataset, DatasetCategory, DatasetFile, DatasetRecord, StorageKind, Station
from app.models.visualize import VisualizationJob, VizJobStatus
from app.schemas.visualize import SpatialPointSchema, SpatialRequest
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="visualize.run_spatial_interpolation", bind=True, max_retries=2)
def run_spatial_interpolation(self, job_id: str) -> dict:
    """Heavy spatial-interpolation fast path's counterpart — large point-
    count/resolution combinations dispatch here instead of computing
    in-process (Master Plan §3 Phase 7 task 2). Mirrors extraction.py's
    run_extraction/process_extraction split exactly."""
    with get_sync_db() as db:
        return process_spatial_job(db, job_id, celery_task_id=self.request.id)


def process_spatial_job(db, job_id: str, *, celery_task_id: str | None = None) -> dict:
    job = db.get(VisualizationJob, job_id)
    if job is None:
        logger.error("visualize.job_not_found", job_id=job_id)
        return {"status": "failed", "reason": "job not found"}

    job.status = VizJobStatus.PROCESSING.value
    if celery_task_id:
        job.celery_task_id = celery_task_id
    db.commit()

    try:
        result = _run_spatial_job(db, job)
    except Exception as exc:
        logger.exception("visualize.spatial_job_failed", job_id=job_id)
        job.status = VizJobStatus.FAILED.value
        job.error_message = f"Unexpected error during interpolation: {exc}"
        db.commit()
        return {"status": "failed", "reason": str(exc)}

    job.status = VizJobStatus.COMPLETE.value
    job.result = result
    job.completed_at = datetime.now(UTC)
    db.commit()

    return {"status": "complete", **result}


def _run_spatial_job(db, job: VisualizationJob) -> dict:
    # Imported lazily to avoid a service (async) <-> task (sync) import
    # cycle; the pure numpy/scipy interpolation core has no async
    # dependency, only the DB fetch does, which is re-implemented here
    # against the sync session (mirrors extraction.py's pattern of the
    # Celery task owning its own sync data access).
    from app.services.visualize_service import compute_interpolation

    params = SpatialRequest.model_validate(job.params)

    latest_per_station_sq = (
        select(
            DatasetRecord.station_id,
            func.max(DatasetRecord.time).label("max_time"),
        )
        .where(DatasetRecord.parameter == params.parameter, DatasetRecord.station_id.is_not(None))
        .group_by(DatasetRecord.station_id)
    )
    if params.category:
        latest_per_station_sq = latest_per_station_sq.where(
            DatasetRecord.dataset_id.in_(
                select(Dataset.id).where(Dataset.category.has(DatasetCategory.name == params.category))
            )
        )
    if params.station:
        latest_per_station_sq = latest_per_station_sq.where(
            DatasetRecord.station_id.in_(select(Station.id).where(Station.code == params.station))
        )
    if params.date_from:
        latest_per_station_sq = latest_per_station_sq.where(DatasetRecord.time >= params.date_from)
    if params.date_to:
        latest_per_station_sq = latest_per_station_sq.where(DatasetRecord.time <= params.date_to)
    latest_subq = latest_per_station_sq.subquery()

    query = (
        select(Station.name, Station.lat, Station.lon, DatasetRecord.value)
        .join(latest_subq, latest_subq.c.station_id == Station.id)
        .join(
            DatasetRecord,
            (DatasetRecord.station_id == latest_subq.c.station_id)
            & (DatasetRecord.time == latest_subq.c.max_time)
            & (DatasetRecord.parameter == params.parameter),
        )
    )
    rows = db.execute(query).all()
    points = [
        SpatialPointSchema(station=row.name, lat=float(row.lat), lon=float(row.lon), value=float(row.value))
        for row in rows
    ]

    # PLAN.md Phase 5: same additive PARQUET/CHUNKED_ARRAY routing as
    # visualize_service.get_spatial_points' sync fast path — this Celery
    # task is the async-dispatch counterpart for oversized requests, and
    # must combine both storage kinds identically, not just the legacy
    # SQL points, or a heavy request would silently drop new-architecture
    # data that a light request (computed in-process) would have included.
    if params.dataset_id:
        from app.services import gridded_query_service, tabular_query_service
        from app.services.catalog_service import RecordsFilter

        record_filter = RecordsFilter(
            date_from=params.date_from,
            date_to=params.date_to,
            lat_min=params.lat_min,
            lat_max=params.lat_max,
            lon_min=params.lon_min,
            lon_max=params.lon_max,
            depth_min=params.depth_min,
            depth_max=params.depth_max,
            station=params.station,
        )
        non_legacy_files = (
            db.execute(
                select(DatasetFile).where(
                    DatasetFile.dataset_id == params.dataset_id,
                    DatasetFile.storage_kind.in_(
                        [StorageKind.PARQUET.value, StorageKind.CHUNKED_ARRAY.value]
                    ),
                )
            )
            .scalars()
            .all()
        )
        for file in non_legacy_files:
            if file.storage_kind == StorageKind.PARQUET.value:
                file_points = tabular_query_service.get_spatial_points(file, params.parameter, record_filter)
            else:
                file_points = gridded_query_service.get_spatial_points(file, params.parameter, record_filter)
            points.extend(
                SpatialPointSchema(station=p.station, lat=p.lat, lon=p.lon, value=p.value)
                for p in file_points
            )

    grid, method_used = compute_interpolation(points, params)

    return {
        "points": [p.model_dump(mode="json") for p in points],
        "grid": grid.model_dump(mode="json"),
        "method_used": method_used,
    }
