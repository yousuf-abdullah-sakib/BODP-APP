"""Dataset Default-View Snapshot feature — generates a small, precomputed
JSON artifact per dataset (capped sample rows + DatasetVariable stats +
one default chart) so the Data Page and Visualize page can serve an
unfiltered "just opened this dataset" view instantly, without the live
query path (which is, unintuitively, the MOST expensive shape — nothing
to prune the scan by; see catalog.py's own benchmark comment: ~24.8s for
a real 19.6M-cell dataset with zero filters).

Dispatched via .delay() right after ingestion's existing success block in
worker/tasks/ingestion.py — fire-and-forget, never inline in
_run_ingestion, so a slow or failed snapshot build can never delay or
block a dataset being marked ready. Regenerates the WHOLE dataset's
snapshot every time (not incremental), which is correct and cheap: a
snapshot's size is capped by construction (6 preview rows + a monthly
time series), never proportional to the underlying dataset's real size.

Async bridging: catalog_service.get_filtered_records and
visualize_service.get_timeseries are async (AsyncSession-based) — reusing
them directly (rather than re-deriving the same query logic in sync SQL,
which every other Celery task in this codebase does today) avoids
duplicating substantial query logic. Follows worker/tasks/bulk_import.py's
established asyncio-bridging pattern exactly (see that file's
_run_async_in_thread/_run_transfer_with_fresh_engine docstrings for the
full reasoning): a dedicated ThreadPoolExecutor + a fresh AsyncEngine
created and disposed entirely within one asyncio.run() call, since
asyncpg connections are permanently bound to the event loop that created
them and this task's caller (Celery's task_always_eager test mode) may
already be running inside pytest-asyncio's own loop.
"""
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_sync_db
from app.models.catalog import Dataset, DatasetVariable
from app.services.storage.keys import snapshot_key
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)

_PREVIEW_ROW_LIMIT = 6
_STORAGE_BACKEND = "vps_minio"


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


async def _build_snapshot_payload(session_factory, dataset_id: uuid.UUID) -> dict | None:
    """Returns the snapshot's JSON-ready dict, or None if the dataset no
    longer exists (a race with deletion — the caller skips the write
    entirely rather than persisting a snapshot for a gone dataset)."""
    from app.services import catalog_service
    from app.services.visualize_service import _compute_statistics, _non_legacy_dataset_files, get_timeseries
    from app.schemas.visualize import StatisticsRequest, TimeSeriesRequest

    async with session_factory() as db:
        dataset = await db.get(Dataset, dataset_id)
        if dataset is None:
            return None

        record_filter = catalog_service.RecordsFilter()
        preview_rows, matching_count, dataset_total_count, breakdown = await catalog_service.get_filtered_records(
            db, dataset_id, record_filter, preview_limit=_PREVIEW_ROW_LIMIT
        )
        records_payload = {
            "preview": [
                {
                    "id": r.id,
                    "time": r.time,
                    "location": r.location,
                    "depth_m": float(r.depth_m) if r.depth_m is not None else None,
                    "parameter": r.parameter,
                    "value": float(r.value),
                    "unit": r.unit,
                    "platform": r.platform,
                    "format": r.format,
                    "processing_level": r.processing_level,
                    "quality_flag": r.quality_flag,
                }
                for r in preview_rows
            ],
            "matching_count": matching_count,
            "dataset_total_count": dataset_total_count,
            "quality_breakdown": {
                "normal": breakdown.get("normal", 0),
                "caution": breakdown.get("caution", 0),
                "alert": breakdown.get("alert", 0),
            },
        }

        variable_rows = (
            await db.execute(select(DatasetVariable).where(DatasetVariable.dataset_id == dataset_id))
        ).scalars().all()
        variable_stats = [
            {
                "name": v.name,
                "min_value": float(v.min_value) if v.min_value is not None else None,
                "max_value": float(v.max_value) if v.max_value is not None else None,
                "distinct_values": v.distinct_values,
            }
            for v in variable_rows
        ]

        # First approved Visualization Variable, in the SAME order the
        # frontend's dataset selector actually sees (get_visualizable_
        # datasets' .order_by(Dataset.title, DatasetVariable.name), a real
        # SQL ORDER BY) — a real bug was caught here: this used to sort
        # variable_rows with a plain Python sorted(key=lambda v: v.name),
        # which orders case-sensitively (all uppercase before all
        # lowercase, e.g. "VHM0" before "so"), while Postgres's ORDER BY
        # uses the database's collation and produced a DIFFERENT first
        # element for any dataset with mixed-case variable names (a real
        # NetCDF dataset with variables ["so", "thetao", "VHM0", "VTM10",
        # "wind_speed"] built its snapshot's default_chart for "VHM0"
        # while the frontend's own auto-selected parameter was "so" — the
        # two never matched, so the snapshot was silently never served
        # for that dataset). Re-querying with an explicit ORDER BY here
        # (rather than sorting variable_rows in Python) guarantees this
        # generator agrees with get_visualizable_datasets by construction,
        # not by coincidentally reimplementing the same collation.
        default_parameter_row = (
            await db.execute(
                select(DatasetVariable.name)
                .where(
                    DatasetVariable.dataset_id == dataset_id,
                    DatasetVariable.roles.any("visualization_variable"),
                    DatasetVariable.is_dimension.is_(False),
                )
                .order_by(DatasetVariable.name)
                .limit(1)
            )
        ).first()
        default_parameter = default_parameter_row[0] if default_parameter_row else None

        default_chart = None
        if default_parameter is not None:
            date_from, date_to = await catalog_service.get_dataset_temporal_extent(db, dataset_id)
            ts_request = TimeSeriesRequest(
                dataset_id=dataset_id,
                parameter=default_parameter,
                resolution="monthly",
                date_from=date_from,
                date_to=date_to,
            )
            try:
                ts_response = await get_timeseries(db, ts_request)
                default_chart = {
                    "parameter": default_parameter,
                    "response": ts_response.model_dump(mode="json"),
                }
            except Exception:
                # A default chart is a nice-to-have, not a hard
                # requirement for the snapshot to be useful — sample rows
                # and stats are still valid even if the chart build fails
                # for this dataset (e.g. an edge-case shape it doesn't
                # handle yet). Never let this abort the whole snapshot.
                logger.warning(
                    "snapshot.default_chart_failed", dataset_id=str(dataset_id), parameter=default_parameter,
                    exc_info=True,
                )

        # Phase 1 (Visualize Performance plan): the Statistics tab has no
        # single canonical "default" the way Temporal Analysis does, but
        # its box_plot/histogram/decomposition/calendar_heatmap for the
        # SAME default_parameter (full extent, no other filter) is exactly
        # as reusable a precompute target — same reasoning as default_chart
        # above, same request shape, computed here rather than as a
        # separate artifact/storage key so staleness tracking (Dataset.
        # version vs snapshot_version) stays the single existing mechanism.
        #
        # Calls _compute_statistics directly, NOT the get_statistics
        # dispatch wrapper (Phase 4) — the wrapper's job is deciding
        # sync-vs-Celery for a live HTTP request; a Celery task computing
        # a precomputed default must always compute synchronously in
        # place (dispatching a second job from inside a job would be
        # both wrong and pointless — precompute IS the mechanism that
        # avoids ever needing the heavy-job path for this exact request
        # shape).
        default_statistics = None
        if default_parameter is not None:
            date_from, date_to = await catalog_service.get_dataset_temporal_extent(db, dataset_id)
            stats_request = StatisticsRequest(
                dataset_id=dataset_id,
                parameter=default_parameter,
                resolution="monthly",
                date_from=date_from,
                date_to=date_to,
            )
            try:
                non_legacy_grouped = await _non_legacy_dataset_files(db, dataset_id)
                stats_response = await _compute_statistics(db, stats_request, non_legacy_grouped)
                default_statistics = {
                    "parameter": default_parameter,
                    "response": stats_response.model_dump(mode="json"),
                }
            except Exception:
                logger.warning(
                    "snapshot.default_statistics_failed", dataset_id=str(dataset_id), parameter=default_parameter,
                    exc_info=True,
                )

        return {
            "dataset_version": dataset.version,
            "generated_at": datetime.now(UTC).isoformat(),
            "records": records_payload,
            "variable_stats": variable_stats,
            "default_chart": default_chart,
            "default_statistics": default_statistics,
        }


async def _generate_async(dataset_id: uuid.UUID) -> dict:
    engine = create_async_engine(str(settings.DATABASE_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        payload = await _build_snapshot_payload(session_factory, dataset_id)
        return payload
    finally:
        await engine.dispose()


def _generate_in_thread(dataset_id: uuid.UUID) -> dict | None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, _generate_async(dataset_id)).result()


@celery_app.task(name="snapshots.generate_dataset_snapshot", bind=True, max_retries=2)
def generate_dataset_snapshot(self, dataset_id: str) -> dict:
    dataset_uuid = uuid.UUID(dataset_id)

    payload = _generate_in_thread(dataset_uuid)
    if payload is None:
        logger.info("snapshot.dataset_gone", dataset_id=dataset_id)
        return {"status": "skipped", "reason": "dataset not found"}

    body = json.dumps(payload, default=_json_default).encode("utf-8")

    bucket = default_bucket_for(_STORAGE_BACKEND)
    storage = get_storage_backend(_STORAGE_BACKEND)
    storage.ensure_bucket(bucket)
    storage.put(bucket, snapshot_key(dataset_uuid), BytesIO(body), content_type="application/json")

    # snapshot_version is set only AFTER the storage write above
    # succeeds — an in-flight/failed write must never be pointed to by a
    # "fresh" version number (mirrors reports.py's Report.output_
    # storage_key: set once the artifact is durably written, never
    # before).
    with get_sync_db() as db:
        dataset = db.get(Dataset, dataset_uuid)
        if dataset is not None:
            dataset.snapshot_version = payload["dataset_version"]
            db.commit()

    logger.info("snapshot.generated", dataset_id=dataset_id, dataset_version=payload["dataset_version"])
    return {"status": "complete", "dataset_version": payload["dataset_version"]}
