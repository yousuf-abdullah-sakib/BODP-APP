import math
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
import structlog
from celery.exceptions import SoftTimeLimitExceeded
from geoalchemy2 import Geometry
from sqlalchemy import cast, delete, func, select

from app.core.config import settings
from app.core.database import get_sync_db
from app.models.catalog import Dataset, DatasetFile, DatasetRecord, DatasetVariable, StorageKind, VariableDataType
from app.models.uploads import QualityIssue, Upload, UploadStatus
from app.services.parsers import ParserError, get_parser_for_format
from app.services.parsers.base import DataShape, ParsedFileMetadata, ProcessedArtifact
from app.services.storage.base import StorageService, UploadItem
from app.services.storage.keys import processed_key, processed_prefix
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Non-variable columns every tidy Parquet artifact may carry alongside the
# actual observation values — never turned into their own DatasetRecord rows.
_NON_VARIABLE_SUFFIXES = ("_bnds", "_bounds")

_RECORD_BATCH_SIZE = 5_000

# DatasetRecord COPY commit size — benchmarked in
# test_ingestion_benchmark.py against 250K/500K/1M rows at a 2M-row
# scale: 250K was both the FASTEST (12,041 rows/s vs 10,078 at 500K and
# 9,565 at 1M) and the most memory-bounded (+53MB peak RSS delta vs
# +199MB and +378MB) — larger chunks held more Python row-tuples live at
# once without a throughput benefit at this row width, so smaller
# clearly won on every axis measured here. See that test file's module
# docstring for the full measured numbers.
_COPY_CHUNK_ROWS = 250_000

_DEFAULT_QUALITY_FLAG = "normal"


def _raw_sync_dsn() -> str:
    """DATABASE_URL_SYNC carries SQLAlchemy's '+psycopg' dialect marker
    (postgresql+psycopg://...), which a plain psycopg.connect() call
    doesn't understand — strips it for the dedicated COPY connection
    below, which is intentionally NOT the SQLAlchemy-managed sync_engine
    (see _write_dataset_records' docstring for why)."""
    return settings.DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://")

# Matches app.models.catalog._MAX_SAMPLED_DISTINCT_VALUES — kept as a
# separate constant here rather than importing the private one, since this
# module's sampling loop is what actually enforces the cap while scanning.
_MAX_SAMPLED_DISTINCT_VALUES = 50


def _widen(current: date | None, candidate: date | None, *, take_min: bool) -> date | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return min(current, candidate) if take_min else max(current, candidate)


class IngestionCancelled(Exception):
    """Raised internally when a checkpoint detects the admin/user cancelled
    this upload (Upload.status flipped to CANCELLED by
    dataset_multipart_upload_service.cancel_upload while this task was
    running) — caught in process_dataset_file to run cleanup and stop,
    never treated as a FAILED ingestion.

    Carries whatever processed-artifact storage location (if any) was
    already written before cancellation was detected, so the cleanup
    handler knows what to delete without needing dataset_file.file_metadata
    (which is only set — and only committed — after this point, i.e.
    after cancellation can still interrupt).

    processed_is_prefix distinguishes a single-object artifact (Parquet/
    COG — cleaned up via storage.delete()) from a Zarr store's many
    chunk objects under a shared prefix (PLAN.md Phase 5 — cleaned up via
    storage.delete_prefix() instead)."""

    def __init__(
        self,
        *,
        processed_bucket: str | None = None,
        processed_key: str | None = None,
        processed_is_prefix: bool = False,
    ):
        self.processed_bucket = processed_bucket
        self.processed_key = processed_key
        self.processed_is_prefix = processed_is_prefix
        super().__init__("Ingestion cancelled")


def _checkpoint(
    db,
    upload: Upload | None,
    *,
    stage: str | None = None,
    pct: int | None = None,
    written_processed_bucket: str | None = None,
    written_processed_key: str | None = None,
    written_processed_is_prefix: bool = False,
) -> None:
    """Re-reads Upload.status from the DB and raises IngestionCancelled if
    it's been flipped to CANCELLED since this task started. celery's own
    task revocation (control.revoke(terminate=True)) is a SIGTERM that
    cannot interrupt a blocking library call already in progress (e.g.
    mid xr.open_dataset() read) — this cooperative checkpoint, called
    between discrete steps, is the actual mechanism that makes
    cancellation during processing take effect promptly and safely rather
    than relying solely on the signal.

    When stage/pct are given, this is also the single place real
    Upload.progress_stage/progress_pct values get written — reusing the
    DB round-trip _checkpoint already does rather than adding a second one
    just for progress reporting. Every ingestion stage boundary already
    calls this for cancellation detection, so real progress is a side
    effect of a mechanism that already existed, not new plumbing."""
    if upload is None:
        return
    db.refresh(upload)
    if upload.status == UploadStatus.CANCELLED.value:
        raise IngestionCancelled(
            processed_bucket=written_processed_bucket,
            processed_key=written_processed_key,
            processed_is_prefix=written_processed_is_prefix,
        )
    if stage is not None:
        upload.progress_stage = stage
        upload.progress_pct = pct
        db.commit()


def _set_upload_terminal_state(upload_id: str | None, *, status: str, error_message: str | None = None) -> None:
    """Persists Upload.status/.error_message on a BRAND NEW session,
    never the session that was active when a failure occurred. Verified
    live (see the "con june all day.mat" incident this fixes): a
    SoftTimeLimitExceeded — or any exception — can leave the original
    session's underlying DBAPI connection in a broken state
    (psycopg.OperationalError: "another command is already in
    progress"), and attempting the terminal-state commit on THAT session
    can itself raise (PendingRollbackError cascading into
    OperationalError), leaving Upload permanently stuck at
    status=processing. A fresh get_sync_db() call opens an independent
    connection from the pool, so this write's success never depends on
    whatever state the failed ingestion attempt left its own session in.
    """
    if not upload_id:
        return
    try:
        with get_sync_db() as fresh_db:
            upload = fresh_db.get(Upload, upload_id)
            if upload is not None:
                upload.status = status
                if error_message is not None:
                    upload.error_message = error_message
                fresh_db.commit()
    except Exception:
        # This is the last-resort terminal-state write — if even a brand
        # new session/connection can't reach the DB at all (e.g. Postgres
        # itself is down), there is nothing further this function can do;
        # log and move on rather than raise a second exception on top of
        # whatever caused the original failure.
        logger.exception("ingestion.terminal_state_write_failed", upload_id=upload_id, status=status)


@celery_app.task(name="ingestion.process_dataset_file", bind=True, max_retries=2)
def process_dataset_file(self, dataset_file_id: str, upload_id: str | None = None) -> dict:
    """Runs after a raw file has been uploaded to storage (Master Plan §3
    Phase 2 task 4): parse it, auto-detect spatial/temporal extent and
    variables, write a query-optimized Parquet copy to the processed/ tier,
    and update dataset_files / datasets / uploads accordingly.

    Failure at any stage marks the upload 'failed' with a clear reason
    rather than leaving it stuck 'processing' or silently succeeding on bad
    data (Master Plan §3 Phase 2 quality check). A cancellation mid-run
    (see IngestionCancelled/_checkpoint above) is handled distinctly from
    failure — it cleans up whatever this task already wrote and marks the
    upload CANCELLED, never FAILED and never left 'processing'.
    """
    with get_sync_db() as db:
        dataset_file = db.get(DatasetFile, dataset_file_id)
        if dataset_file is None:
            logger.error("ingestion.dataset_file_not_found", dataset_file_id=dataset_file_id)
            return {"status": "failed", "reason": "dataset_file not found"}

        upload = db.get(Upload, upload_id) if upload_id else None
        if upload:
            if upload.status == UploadStatus.CANCELLED.value:
                # Cancelled between being queued and this task actually
                # starting — never even begin ingestion.
                return {"status": "cancelled"}
            upload.status = UploadStatus.PROCESSING.value
            upload.celery_task_id = self.request.id
            db.commit()

        try:
            result = _run_ingestion(db, dataset_file, upload=upload)
        except IngestionCancelled as exc:
            logger.info("ingestion.cancelled", dataset_file_id=dataset_file_id)
            try:
                _cleanup_cancelled_ingestion(
                    db,
                    dataset_file,
                    processed_bucket=exc.processed_bucket,
                    processed_key=exc.processed_key,
                    processed_is_prefix=exc.processed_is_prefix,
                )
            except Exception:
                # Best-effort — db's session/connection may itself be
                # unhealthy at this point. The terminal-state write below
                # uses a fresh session regardless, so Upload still
                # reliably reaches CANCELLED even if this cleanup failed;
                # any DatasetRecord rows this run wrote remain deletable
                # later by dataset_file_id (they are never silently
                # treated as valid — DatasetFile itself failed to reach
                # its success commit).
                logger.exception("ingestion.cancel_cleanup_failed", dataset_file_id=dataset_file_id)
            _set_upload_terminal_state(
                upload_id, status=UploadStatus.CANCELLED.value, error_message="Cancelled during processing."
            )
            return {"status": "cancelled"}
        except ZarrUploadFailed as exc:
            # _upload_zarr_store already deleted whatever it managed to
            # write under processed_prefix before raising (it's the only
            # thing that knows the exact set of keys it attempted) — the
            # remaining cleanup here is the same orphaned-DatasetFile-row
            # gap every cancellation path already guards against:
            # dataset_file was created at raw-upload time, before this
            # task ever ran, so a failed ingestion must not leave it
            # behind as an empty shell. processed_bucket=None tells
            # _cleanup_cancelled_ingestion there is no prefix left to
            # delete (already done) — it should only remove the
            # DatasetRecord/DatasetFile rows.
            logger.warning(
                "ingestion.zarr_upload_permanently_failed",
                dataset_file_id=dataset_file_id,
                failed_count=exc.failed_count,
                total_count=exc.total_count,
            )
            try:
                _cleanup_cancelled_ingestion(db, dataset_file, processed_bucket=None, processed_key=None)
            except Exception:
                logger.exception("ingestion.zarr_upload_failure_cleanup_failed", dataset_file_id=dataset_file_id)
            _set_upload_terminal_state(
                upload_id,
                status=UploadStatus.FAILED.value,
                error_message=(
                    f"Failed to upload {exc.failed_count} of {exc.total_count} processed data chunk(s) "
                    "to storage after multiple retries. This may indicate a temporary storage issue — "
                    "please try again, or contact an administrator if this persists."
                ),
            )
            return {"status": "failed", "reason": "zarr_upload_failed"}
        except ParserError as exc:
            logger.warning(
                "ingestion.parse_rejected",
                dataset_file_id=dataset_file_id,
                reason=str(exc),
            )
            _set_upload_terminal_state(upload_id, status=UploadStatus.FAILED.value, error_message=str(exc))
            return {"status": "failed", "reason": str(exc)}
        except SoftTimeLimitExceeded:
            # Phase 2: the per-dispatch soft time limit (celery_app.py's
            # ingestion_soft_time_limit_seconds, computed from the file's
            # actual size) was exceeded — a clear, specific reason rather
            # than falling through to the generic "Unexpected error"
            # message below. Whatever this run had already written to the
            # DB is cleaned up the same way an explicit cancellation is —
            # from the data's perspective it's the identical situation:
            # ingestion did not finish, nothing partial should be left
            # behind as if it had. Unlike a checkpoint-detected
            # cancellation, a soft time limit can fire at any point, not
            # only at a known checkpoint boundary, so this cannot
            # reliably identify (and therefore cannot delete) a
            # processed/ object that might have just been written — a
            # rare, documented gap versus a deliberate cancellation's
            # precise cleanup.
            #
            # Live-confirmed failure mode this specifically fixes: a
            # SoftTimeLimitExceeded raised mid-COPY-chunk-commit can leave
            # db's underlying DBAPI connection in a broken state
            # ("another command is already in progress"), making the
            # cleanup call below itself raise — caught here so the
            # terminal-state write still happens on a fresh session
            # regardless.
            logger.warning("ingestion.soft_time_limit_exceeded", dataset_file_id=dataset_file_id)
            try:
                _cleanup_cancelled_ingestion(db, dataset_file, processed_bucket=None, processed_key=None)
            except Exception:
                logger.exception("ingestion.timeout_cleanup_failed", dataset_file_id=dataset_file_id)
            _set_upload_terminal_state(
                upload_id,
                status=UploadStatus.FAILED.value,
                error_message=(
                    "Processing exceeded the time limit for a file of this size. "
                    "This may indicate an unusually large or complex file — contact an administrator "
                    "if this persists."
                ),
            )
            return {"status": "failed", "reason": "soft_time_limit_exceeded"}
        except Exception as exc:
            logger.exception("ingestion.unexpected_error", dataset_file_id=dataset_file_id)
            _set_upload_terminal_state(
                upload_id, status=UploadStatus.FAILED.value, error_message=f"Unexpected error during processing: {exc}"
            )
            return {"status": "failed", "reason": str(exc)}

        if upload:
            upload.status = UploadStatus.COMPLETE.value
            upload.dataset_file_id = dataset_file.id
            upload.progress_stage = "complete"
            upload.progress_pct = 100
            db.commit()

            # Mirrors the prototype's auto-generated QC entry on successful
            # upload completion (Master Plan frontend audit, DataUploadSection).
            if result.get("shape") == DataShape.RASTER.value:
                count_desc = f"{result['record_count']} pixels"
                names_desc = "band"
            elif result.get("shape") == DataShape.GRIDDED.value:
                count_desc = f"{result['record_count']} grid elements"
                names_desc = "variable"
            else:
                count_desc = f"{result['record_count']} records"
                names_desc = "variable"
            db.add(
                QualityIssue(
                    dataset_id=dataset_file.dataset_id,
                    issue_type="Schema Mismatch",
                    severity="low",
                    status="resolved",
                    detail=(
                        f"File '{dataset_file.file_name}' passed schema and range "
                        f"checks on ingestion ({count_desc}, "
                        f"{len(result['variables'])} {names_desc}(s) detected)."
                    ),
                )
            )
            db.commit()

        # Dataset Default-View Snapshot feature: fire-and-forget, dispatched
        # unconditionally on any successful ingestion (not nested inside
        # `if upload:` above — bulk-import-sourced ingestion has no Upload
        # row at all, see _run_ingestion's `upload: Upload | None = None`,
        # and must still get a fresh snapshot). Dispatched AFTER every
        # commit above so a slow/failed snapshot build can never delay or
        # block this dataset being marked ready. Regenerates the whole
        # dataset's snapshot (not just this file's contribution) since a
        # dataset can have multiple DatasetFile rows and the snapshot must
        # merge across all of them, same as the live query path does.
        from app.worker.tasks.snapshots import generate_dataset_snapshot

        generate_dataset_snapshot.delay(str(dataset_file.dataset_id))

        return {"status": "complete", **result}


class ZarrUploadFailed(Exception):
    """Raised by _upload_zarr_store when one or more chunk objects
    permanently failed to upload (after boto3's own built-in retries —
    see Settings.STORAGE_MAX_RETRIES) — carries enough detail to log
    which objects failed, and always means some subset of the store's
    objects may now exist in the target bucket under processed_prefix
    while others don't. Caught by _run_ingestion's caller alongside every
    other _run_ingestion failure mode: process_dataset_file's generic
    `except Exception` marks the upload FAILED, but does NOT run
    prefix cleanup itself (unlike IngestionCancelled/SoftTimeLimitExceeded,
    which know exactly what was durably written) — so this exception's
    __init__ deletes the partial prefix itself, before propagating,
    ensuring a permanently-failed Zarr upload never leaves a half-written
    store behind for a later retry to see as if it were complete."""

    def __init__(self, *, failed_count: int, total_count: int, sample_errors: list[str]):
        self.failed_count = failed_count
        self.total_count = total_count
        self.sample_errors = sample_errors
        super().__init__(
            f"{failed_count}/{total_count} Zarr chunk object(s) failed to upload "
            f"after retries (e.g. {'; '.join(sample_errors[:3])})"
        )


# Throttles how often a Zarr store's chunk-upload progress becomes an
# actual Upload.progress_pct DB write — reusing _checkpoint's existing
# round-trip for every single object (potentially thousands) would trade
# the upload bottleneck this whole function exists to fix for a new
# database-write bottleneck instead. Time-based (not count-based) so a
# store with very few, very large chunks (e.g. one huge coordinate array)
# still reports progress promptly, and one with thousands of tiny chunks
# doesn't write on every completion.
_PROGRESS_CHECKPOINT_INTERVAL_SECONDS = 2.0


def _upload_zarr_store(
    storage: StorageService,
    *,
    local_dir: Path,
    bucket: str,
    prefix: str,
    content_type: str | None,
    db,
    upload: Upload | None,
    dataset_file_id,
) -> None:
    """Uploads every file under `local_dir` (a Zarr store — many small
    chunk + metadata files) to `bucket` under `prefix`, one object per
    file, via StorageService.put_many()'s bounded concurrency (Settings.
    STORAGE_UPLOAD_CONCURRENCY) rather than the old one-at-a-time loop —
    see Settings.STORAGE_UPLOAD_CONCURRENCY's and
    Settings.INGESTION_ZARR_TIME_CHUNK_SIZE's docstrings for the real-file
    benchmark this replaced (~641s -> ~53s for a 2.03GB/8-variable/hourly
    NetCDF's ~14,600-chunk store, combining fewer/larger chunks with
    concurrent upload).

    `items` is built as a plain list of (path, key) pairs up front — small
    even at thousands of entries (a path string per chunk, not chunk
    bytes) — and put_many() only opens each file when that specific
    item's own upload actually runs, so this never holds more than
    STORAGE_UPLOAD_CONCURRENCY files' worth of bytes in memory at once
    regardless of how many total chunks the store has.

    Raises ZarrUploadFailed (after deleting whatever was durably written
    under `prefix`) if any object permanently failed — the caller must
    treat this exactly like any other _run_ingestion failure (the
    existing generic `except Exception` handler in process_dataset_file
    already does, unchanged). Does NOT itself detect Upload cancellation
    (SoftTimeLimitExceeded/checkpoint-based cancellation still work
    exactly as before, since ThreadPoolExecutor's worker threads simply
    get abandoned when the Celery task process is interrupted — nothing
    new for cancellation to preserve here beyond what already existed for
    the old sequential loop, which had the same property)."""
    items = [
        UploadItem(local_path=chunk_file, key=f"{prefix}/{chunk_file.relative_to(local_dir).as_posix()}")
        for chunk_file in local_dir.rglob("*")
        if chunk_file.is_file()
    ]
    total = len(items)

    logger.info(
        "ingestion.zarr_upload_started",
        dataset_file_id=str(dataset_file_id),
        total_objects=total,
        concurrency=settings.STORAGE_UPLOAD_CONCURRENCY,
    )
    start = time.monotonic()
    last_checkpoint = start

    def on_progress(completed: int, total_count: int) -> None:
        nonlocal last_checkpoint
        now = time.monotonic()
        if now - last_checkpoint < _PROGRESS_CHECKPOINT_INTERVAL_SECONDS and completed < total_count:
            return
        last_checkpoint = now
        pct = round(100 * completed / total_count) if total_count else 100
        _checkpoint(
            db,
            upload,
            stage="uploading_processed",
            pct=pct,
            written_processed_bucket=bucket,
            written_processed_key=prefix,
            written_processed_is_prefix=True,
        )

    results = storage.put_many(
        bucket,
        items,
        content_type=content_type,
        concurrency=settings.STORAGE_UPLOAD_CONCURRENCY,
        on_progress=on_progress,
    )

    failed = [r for r in results if not r.ok]
    duration = time.monotonic() - start
    total_bytes = sum(item.local_path.stat().st_size for item in items)

    if failed:
        logger.error(
            "ingestion.zarr_upload_failed",
            dataset_file_id=str(dataset_file_id),
            total_objects=total,
            failed_objects=len(failed),
            sample_errors=[r.error for r in failed[:5]],
        )
        # Partial store: delete everything written under this prefix so a
        # retry (or a later query) never sees a half-complete Zarr store
        # as if it were valid — same cleanup call
        # _cleanup_cancelled_ingestion uses for a cancelled Zarr upload.
        try:
            storage.delete_prefix(bucket, prefix)
        except Exception:
            logger.exception("ingestion.zarr_partial_upload_cleanup_failed", prefix=prefix)
        raise ZarrUploadFailed(
            failed_count=len(failed), total_count=total, sample_errors=[r.error or "" for r in failed]
        )

    throughput_mb_s = (total_bytes / 1024 / 1024 / duration) if duration > 0 else 0.0
    logger.info(
        "ingestion.zarr_upload_completed",
        dataset_file_id=str(dataset_file_id),
        total_objects=total,
        total_bytes=total_bytes,
        duration_seconds=round(duration, 1),
        throughput_mb_s=round(throughput_mb_s, 1),
    )


def _run_ingestion(db, dataset_file: DatasetFile, *, upload: Upload | None = None) -> dict:
    extension = (dataset_file.file_format or "").lower().lstrip(".")
    parser = get_parser_for_format(extension)

    storage = get_storage_backend(dataset_file.storage_backend)

    # Initial stage write, before the (potentially multi-GB, multi-minute)
    # download even starts — without this, the UI would show nothing at
    # all ("Processing…" with no stage) for however long the download
    # takes, which for a large file is the single longest silent gap.
    _checkpoint(db, upload, stage="downloading", pct=0)

    with tempfile.TemporaryDirectory(prefix="bodp_ingest_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        raw_local_path = tmp_dir_path / "raw_input"

        body = storage.get(dataset_file.storage_bucket, dataset_file.storage_key)
        with open(raw_local_path, "wb") as f:
            while chunk := body.read(1024 * 1024):
                f.write(chunk)

        # Checkpoint 1: after the (potentially large) raw download, before
        # the expensive parse/convert step — cancelling during a multi-GB
        # download shouldn't also pay for parsing a file about to be thrown
        # away.
        _checkpoint(db, upload, stage="parsing", pct=None)

        metadata = parser.parse(raw_local_path)
        # Format-agnostic: tabular parsers write Parquet, GRIDDED parsers
        # write Zarr, GeoTIFF writes a re-tiled COG — the ingestion task
        # doesn't need to know which, except to decide how to upload it
        # (one object vs. many chunk objects under a prefix) below.
        artifact = parser.to_processed(raw_local_path, tmp_dir_path)

        # Checkpoint 2: after conversion, before uploading the processed
        # artifact — the step most worth skipping if cancellation arrived
        # while conversion (the slowest step for a large NetCDF/GeoTIFF)
        # was running.
        _checkpoint(db, upload, stage="uploading_processed", pct=None)

        processed_bucket = default_bucket_for(dataset_file.storage_backend)

        if artifact.is_zarr:
            # PLAN.md Phase 5: a Zarr store is a directory of many small
            # chunk + metadata files, not one blob — upload every file
            # under artifact.local_dir as its own object under a shared
            # prefix (enables genuine chunk-range reads later; a single
            # zipped object would require a full download to read at
            # all). processed_object_key stays None; the prefix is what
            # gets recorded/cleaned-up/queried instead.
            processed_object_key = None
            processed_object_prefix = processed_prefix(dataset_file.dataset_id, dataset_file.id)
            _upload_zarr_store(
                storage,
                local_dir=artifact.local_dir,
                bucket=processed_bucket,
                prefix=processed_object_prefix,
                content_type=artifact.content_type,
                db=db,
                upload=upload,
                dataset_file_id=dataset_file.id,
            )
            storage_kind = StorageKind.CHUNKED_ARRAY.value
        else:
            processed_object_key = processed_key(
                dataset_file.dataset_id, dataset_file.id, extension=artifact.file_extension
            )
            processed_object_prefix = None
            with open(artifact.local_path, "rb") as f:
                storage.put(
                    processed_bucket,
                    processed_object_key,
                    f,
                    content_type=artifact.content_type,
                )
            storage_kind = (
                StorageKind.RASTER.value if metadata.shape == DataShape.RASTER else StorageKind.PARQUET.value
            )

        # Checkpoint 3: after the processed artifact is durably in storage
        # — if cancelled here, _cleanup_cancelled_ingestion (called by the
        # caller's except IngestionCancelled handler) knows to delete this
        # processed object/prefix since dataset_file.file_metadata (set
        # below) hasn't been committed yet to point at it.
        _checkpoint(
            db,
            upload,
            stage="writing_variable_registry",
            pct=None,
            written_processed_bucket=processed_bucket,
            written_processed_key=processed_object_key or processed_object_prefix,
            written_processed_is_prefix=artifact.is_zarr,
        )

        # Must happen before the TemporaryDirectory context exits below —
        # artifact.local_path/local_dir is deleted along with the whole
        # tmp_dir the moment this `with` block ends, so this is the last
        # point it's still readable.
        #
        # PLAN.md Phase 5: new ingestion NEVER writes DatasetRecord rows,
        # for any format/shape — actual observation values live only in
        # MinIO (Parquet/Zarr/COG), queried via tabular_query_service.py
        # (DuckDB) or gridded_query_service.py (xarray), never via a
        # row-per-observation SQL table. _write_dataset_records and its
        # COPY writer remain in the codebase for the legacy/administrative
        # path only (backfill_dataset_records.py, repairing pre-Phase-5
        # data) — nothing in this live ingestion flow calls it.
        variables_registered = _write_variable_registry(
            db, dataset_file=dataset_file, metadata=metadata, artifact=artifact
        )

    dataset_file.spatial_extent = _bbox_wkt(metadata) if _has_bbox(metadata) else None
    dataset_file.temporal_start = metadata.temporal_start
    dataset_file.temporal_end = metadata.temporal_end
    dataset_file.storage_kind = storage_kind

    # variables/record_count are the tabular/gridded-shaped view of "what
    # does this file contain" — for a raster, bands stand in for
    # variables and pixel dimensions stand in for a row count. All three
    # get folded into the same dataset.parameters/record_count aggregates
    # below so catalog search doesn't need to know a file's shape to
    # summarize a dataset.
    content_names = metadata.variables if metadata.shape != DataShape.RASTER else metadata.bands
    content_count = (
        metadata.record_count
        if metadata.shape != DataShape.RASTER
        else (metadata.pixel_width or 0) * (metadata.pixel_height or 0)
    )

    dataset_file.file_metadata = {
        "shape": metadata.shape.value,
        "variables": metadata.variables,
        "dimensions": metadata.dimensions,
        "record_count": metadata.record_count,
        "bands": metadata.bands,
        "pixel_width": metadata.pixel_width,
        "pixel_height": metadata.pixel_height,
        "processed_key": processed_object_key,
        "processed_prefix": processed_object_prefix,
        "processed_bucket": processed_bucket,
        "storage_kind": storage_kind,
        **{k: v for k, v in metadata.extra.items()},
    }

    dataset = db.get(Dataset, dataset_file.dataset_id)
    if dataset is not None:
        # Reaching this point at all means this file's ingestion already
        # succeeded — parse, to_processed, storage upload, and variable-
        # registry write are all upstream, unguarded by any try/except in
        # this function, so a ParserError/IngestionCancelled/other
        # exception at any of those stages propagates out of
        # _run_ingestion entirely (see process_dataset_file's try/except)
        # and this whole block, including this bump, never executes for
        # that attempt. That makes "did we get here" itself the correct
        # success signal for Dataset.version — NOT content_count's
        # truthiness, which was the original (wrong) gate: a genuinely
        # successful ingestion of an empty/zero-content file (e.g. a
        # header-only CSV — csv_parser.py explicitly supports this,
        # "still produce a valid (empty) parquet output") left content_
        # count falsy, so version silently never moved even though a new
        # file, spatial_extent, temporal range, and parameters may have
        # just been added to the dataset. Bumping unconditionally here
        # means DatasetRequest.dataset_version (the coverage snapshot's
        # staleness marker) can never miss a real, successful content
        # change — while a failed or cancelled ingestion still can't
        # reach this line at all, so version stays correctly frozen for
        # those.
        dataset.version = (dataset.version or 1) + 1
        if content_count:
            dataset.record_count = (dataset.record_count or 0) + content_count
        merged_params = set(dataset.parameters or [])
        merged_params.update(content_names)
        dataset.parameters = sorted(merged_params)
        if dataset_file.file_format and dataset_file.file_format not in (dataset.formats or []):
            dataset.formats = sorted(set(dataset.formats or []) | {dataset_file.file_format})
        if dataset_file.spatial_extent is not None:
            # Envelope of the union so the dataset-level bbox covers every
            # file's extent, not just whichever file happened to land last.
            # Both sides are cast to `geometry` explicitly — dataset.spatial_extent
            # may hold a plain WKT string in-session (e.g. assigned by a seed
            # script) rather than a WKBElement, and ST_Union has no
            # geometry/varchar overload, so an implicit cast can't be relied on.
            dataset.spatial_extent = (
                db.scalar(
                    select(
                        func.ST_Envelope(
                            func.ST_Union(
                                cast(dataset.spatial_extent, Geometry),
                                cast(dataset_file.spatial_extent, Geometry),
                            )
                        )
                    )
                )
                if dataset.spatial_extent is not None
                else dataset_file.spatial_extent
            )

    db.commit()

    return {
        "shape": metadata.shape.value,
        "storage_kind": storage_kind,
        "variables": content_names,
        "record_count": content_count,
        "processed_key": processed_object_key,
        "processed_prefix": processed_object_prefix,
        "dataset_variables_registered": variables_registered,
    }


def _cleanup_cancelled_ingestion(
    db,
    dataset_file: DatasetFile,
    *,
    processed_bucket: str | None,
    processed_key: str | None,
    processed_is_prefix: bool = False,
) -> None:
    """Undoes everything a cancelled-mid-run _run_ingestion may have
    already written, so a cancelled upload never leaves an orphaned
    dataset/file behind (Phase 1 cancellation requirement):

    - Any processed/ artifact this run already uploaded (checkpoint 3
      onward) is deleted from storage — dataset_file.file_metadata was
      never committed to point at it (the commit happens after the
      checkpoint that would have caught the cancellation), so nothing else
      in the app knows this object exists; without this it would be a
      silent orphan in the processed/ prefix forever. processed_is_prefix
      (PLAN.md Phase 5) distinguishes a single-object Parquet/COG artifact
      (storage.delete()) from a Zarr store's many chunk objects under a
      shared prefix (storage.delete_prefix()) — deleting only the exact
      key for a Zarr artifact would leave every other chunk object
      orphaned.
    - Any DatasetRecord rows this run already wrote are deleted
      explicitly by dataset_file_id — NOT by relying on db.rollback().
      New ingestion (Phase 5) never writes DatasetRecord rows at all, so
      this is normally a no-op; kept as a safety net for the legacy/
      administrative write path (backfill_dataset_records.py) that still
      calls _write_dataset_records and therefore still needs this same
      cleanup guarantee. _write_dataset_records commits each COPY chunk
      durably as it writes, on a separate connection from this session
      entirely, so a plain rollback here would NOT undo them — an
      explicit delete-by-dataset_file_id is required. Scoped to this
      exact dataset_file_id only — never touches another file's rows in
      the same dataset.
    - The DatasetFile row itself (created before ingestion started, when
      the raw upload completed) is deleted — a cancelled upload must not
      remain as an orphaned Dataset/DatasetFile shell. Now safe to delete
      only after its DatasetRecords are gone, since the FK is NOT ON
      DELETE CASCADE (deliberately — see DatasetRecord.dataset_file_id's
      docstring) and would otherwise raise IntegrityError. The raw object
      in raw/ is deliberately NOT deleted here: it's the original
      uploaded file, and per the storage-preservation guarantee raw files
      are only ever removed via the explicit "permanently delete dataset"
      admin action — an ingestion cancellation is not that.
    """
    db.rollback()

    if processed_bucket and processed_key:
        storage = get_storage_backend(dataset_file.storage_backend)
        if processed_is_prefix:
            storage.delete_prefix(processed_bucket, processed_key)
        else:
            storage.delete(processed_bucket, processed_key)

    db.execute(delete(DatasetRecord).where(DatasetRecord.dataset_file_id == dataset_file.id))
    db.delete(dataset_file)
    db.commit()


def _has_bbox(metadata) -> bool:
    return None not in (
        metadata.spatial_lat_min,
        metadata.spatial_lat_max,
        metadata.spatial_lon_min,
        metadata.spatial_lon_max,
    )


def _bbox_wkt(metadata) -> str:
    """Build a WKT POLYGON from the parsed bounding box for the PostGIS
    spatial_extent column. Degenerate (point-like) extents get a tiny buffer
    so they still form a valid polygon ring rather than a zero-area shape."""
    lat_min, lat_max = metadata.spatial_lat_min, metadata.spatial_lat_max
    lon_min, lon_max = metadata.spatial_lon_min, metadata.spatial_lon_max

    if lat_min == lat_max:
        lat_min -= 1e-6
        lat_max += 1e-6
    if lon_min == lon_max:
        lon_min -= 1e-6
        lon_max += 1e-6

    return (
        f"SRID=4326;POLYGON(("
        f"{lon_min} {lat_min}, {lon_max} {lat_min}, "
        f"{lon_max} {lat_max}, {lon_min} {lat_max}, "
        f"{lon_min} {lat_min}))"
    )


def _resolve_row_time(raw) -> date | None:
    """DatasetRecord.time is a plain Date, but a tidy-table's time column may
    hold a pandas/numpy Timestamp, a python datetime, or (from CSV) an
    already-plain date depending on how pyarrow round-tripped it."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.fromisoformat(str(raw)).date()
    except (ValueError, TypeError):
        return None


def _is_finite_number(value) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _write_dataset_records(
    db,
    *,
    dataset_file: DatasetFile,
    metadata: ParsedFileMetadata,
    artifact: ProcessedArtifact,
    upload: Upload | None = None,
) -> int:
    """Populates DatasetRecord from the tidy Parquet artifact a tabular
    parser (CSV/NetCDF/.mat) just wrote — this is what the catalog's
    filter/records endpoints actually query against
    (catalog_service._apply_record_filters), and what was silently never
    happening before: the pipeline updated dataset.record_count as a
    summary counter but never inserted the underlying rows.

    Deliberately NOT called for raster (GeoTIFF) files — a per-pixel
    DatasetRecord row is meaningless at any real raster resolution (a
    modest 10,000x10,000 GeoTIFF would be 100M rows) and rasters are meant
    to be queried through their COG in processed/, not row-by-row. The
    caller only invokes this for DataShape.TABULAR.

    One DatasetRecord row is written per (observation row, variable) pair
    — the wide tidy table is melted into the long/normalized shape
    DatasetRecord.parameter/.value already assumes (matching how
    seed_catalog.py's demo data is shaped).

    Phase 2: time/lat/lon are independently optional (not every dataset
    has every dimension — a static spatial grid has no time; a
    non-spatial series has no lat/lon). Each row writes whichever of
    these the source file actually has and leaves the rest null, rather
    than requiring all three to be present (the old behavior, which is
    exactly why a timeless file like Wave Data's CSV used to produce zero
    rows). A row is skipped only if it would end up with NONE of
    time / (lat & lon) / station_id set — DatasetRecord has no station
    detection today, so in practice this means: skip only if there's no
    usable time AND no usable lat/lon pair, since a dimension-less row
    (identified by nothing at all) is never useful to keep.

    Writes via PostgreSQL COPY (psycopg3's native cursor.copy(), not ORM
    INSERT) on a completely separate raw connection from `db` — verified
    empirically (not assumed) that sharing db's underlying DBAPI
    connection for a raw commit desynchronizes SQLAlchemy's own
    transaction-state tracking for at least one operation (see
    test_dataset_record_writer.py's session-safety regression test). A
    fully independent connection makes that impossible: this function's
    COPY connection and `db`'s ORM session never share any object, so a
    commit on one can never desync the other. `_checkpoint`'s progress
    writes continue exclusively on `db`, unaffected either way.

    Idempotency: any DatasetRecord rows a PRIOR attempt at this exact
    dataset_file already wrote are deleted (by dataset_file_id, never by
    dataset_id — never touches another file's rows in the same dataset)
    before this attempt writes anything. Safe no-op on a first attempt;
    makes a retry of the same DatasetFile exactly-once regardless of how
    many times it's retried.
    """
    lat_col = metadata.extra.get("lat_col")
    lon_col = metadata.extra.get("lon_col")
    time_col = metadata.extra.get("time_col")
    depth_col = metadata.extra.get("depth_col")
    if not (time_col or (lat_col and lon_col)):
        logger.info(
            "ingestion.dataset_records_skipped_no_dimensions",
            dataset_file_id=str(dataset_file.id),
            reason="file has no detected time column and no detected lat+lon pair",
        )
        return 0

    parquet_file = pq.ParquetFile(artifact.local_path)
    all_columns = set(parquet_file.schema_arrow.names)
    coord_cols = {c for c in (lat_col, lon_col, time_col, depth_col) if c}

    # metadata.variables is each parser's own authoritative list of real
    # data variables (NetCDF: ds.data_vars.keys(); CSV: every column that
    # isn't lat/lon/time). Restricting to it — rather than "every remaining
    # Parquet column" — matters because xarray's to_dataframe() also flattens
    # non-dimensional auxiliary coordinates (e.g. ERA5's "number"/"expver"
    # ensemble/experiment-version scalars) into ordinary-looking columns;
    # those aren't scientific variables and must never become DatasetRecord
    # parameter rows just because they happened to survive the tidy-table
    # conversion.
    variable_columns = sorted(
        col
        for col in all_columns
        if col in metadata.variables
        and col not in coord_cols
        and not col.endswith(_NON_VARIABLE_SUFFIXES)
    )
    if not variable_columns:
        return 0

    # Idempotency: clear any rows a previous, incomplete attempt at this
    # SAME DatasetFile already committed — on the existing ORM session,
    # committed before the COPY connection opens, so a crash mid-COPY on
    # a retry can never see "half old + half new" data.
    db.execute(delete(DatasetRecord).where(DatasetRecord.dataset_file_id == dataset_file.id))
    db.commit()

    total_source_rows = parquet_file.metadata.num_rows or 1
    rows_seen = 0
    written = 0

    copy_columns = "id, dataset_id, dataset_file_id, time, lat, lon, depth_m, parameter, value, quality_flag, format, geom"
    copy_sql = f"COPY dataset_records ({copy_columns}) FROM STDIN"

    copy_conn = psycopg.connect(_raw_sync_dsn())
    try:
        chunk_rows: list[tuple] = []
        last_committed_pct = 0

        def flush_chunk():
            nonlocal chunk_rows, written, last_committed_pct
            if not chunk_rows:
                return
            with copy_conn.cursor() as cur, cur.copy(copy_sql) as copy:
                for row in chunk_rows:
                    copy.write_row(row)
            copy_conn.commit()
            written += len(chunk_rows)
            chunk_rows = []
            # Progress is a function of rows scanned so far, not rows
            # written (a row can be skipped for having no usable
            # dimension) — but is only ever reported here, AFTER this
            # chunk's COPY transaction has actually committed, never
            # before (per the "no batch reported complete before its
            # transaction commits" requirement).
            last_committed_pct = min(99, round(100 * rows_seen / total_source_rows))

        for record_batch in parquet_file.iter_batches(batch_size=_RECORD_BATCH_SIZE):
            table = record_batch.to_pydict()
            row_count = len(table[time_col] if time_col else table[lat_col])
            rows_seen += row_count

            for i in range(row_count):
                lat = table[lat_col][i] if lat_col else None
                lon = table[lon_col][i] if lon_col else None
                has_latlon = _is_finite_number(lat) and _is_finite_number(lon)
                lat = float(lat) if has_latlon else None
                lon = float(lon) if has_latlon else None

                row_time = _resolve_row_time(table[time_col][i]) if time_col else None

                depth_value = table[depth_col][i] if depth_col else None
                depth_m = float(depth_value) if _is_finite_number(depth_value) else None

                if row_time is None and not has_latlon:
                    # Neither dimension resolved for this row (e.g. a
                    # null/unparseable cell in an otherwise-present
                    # column) — this specific row has no way to be
                    # located in time or space, so it's skipped; other
                    # rows in the same file are unaffected.
                    continue

                for col in variable_columns:
                    value = table[col][i]
                    if not _is_finite_number(value):
                        continue
                    chunk_rows.append(
                        (
                            str(uuid.uuid4()),
                            str(dataset_file.dataset_id),
                            str(dataset_file.id),
                            row_time,
                            lat,
                            lon,
                            depth_m,
                            col,
                            float(value),
                            _DEFAULT_QUALITY_FLAG,
                            dataset_file.file_format,
                            f"SRID=4326;POINT({lon} {lat})" if has_latlon else None,
                        )
                    )
                    if len(chunk_rows) >= _COPY_CHUNK_ROWS:
                        flush_chunk()

            # One checkpoint per Parquet batch, same cadence the old
            # per-row implementation used — reports the percentage as of
            # the last COPY chunk that actually committed (never a
            # not-yet-durable count), which for a small/medium file
            # (never reaching one full _COPY_CHUNK_ROWS chunk) stays at 0
            # until the trailing flush_chunk() below, then jumps once —
            # correct per "never report a batch complete before its
            # transaction commits," even though it's less granular than
            # the old per-5K-row cadence for such files.
            _checkpoint(db, upload, stage="writing_records", pct=last_committed_pct)

        flush_chunk()
        if written > 0:
            _checkpoint(db, upload, stage="writing_records", pct=min(99, round(100 * written / total_source_rows)))
    finally:
        copy_conn.close()

    return written


def _upsert_dataset_variable(
    db,
    *,
    dataset_id,
    name: str,
    data_type: str,
    is_dimension: bool,
    min_value: float | None = None,
    max_value: float | None = None,
    distinct_values: list[str] | None = None,
) -> None:
    """Creates or widens a single DatasetVariable row. Widens rather than
    overwrites on a repeat detection (e.g. a second file uploaded into the
    same dataset that redetects the same variable name) — mirrors how
    Dataset.parameters/formats already merge across multiple files rather
    than each upload clobbering the last one's summary."""
    existing = db.execute(
        select(DatasetVariable).where(
            DatasetVariable.dataset_id == dataset_id, DatasetVariable.name == name
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            DatasetVariable(
                dataset_id=dataset_id,
                name=name,
                data_type=data_type,
                is_dimension=is_dimension,
                min_value=min_value,
                max_value=max_value,
                distinct_values=distinct_values[:_MAX_SAMPLED_DISTINCT_VALUES] if distinct_values else None,
            )
        )
        return

    if min_value is not None:
        existing.min_value = min_value if existing.min_value is None else min(float(existing.min_value), min_value)
    if max_value is not None:
        existing.max_value = max_value if existing.max_value is None else max(float(existing.max_value), max_value)
    if distinct_values:
        merged = set(existing.distinct_values or [])
        merged.update(distinct_values)
        existing.distinct_values = sorted(merged)[:_MAX_SAMPLED_DISTINCT_VALUES]


def _write_variable_registry(
    db, *, dataset_file: DatasetFile, metadata: ParsedFileMetadata, artifact: ProcessedArtifact | None
) -> int:
    """Persists every variable/dimension a parser detected as a real
    DatasetVariable row (PLAN.md Phase 2: 'automatic variable detection,
    persisted via a new schema registry table'). Runs for EVERY format,
    including raster (GeoTIFF) — unlike _write_dataset_records, which
    deliberately skips rasters (no per-pixel rows), the registry still
    needs to record that a raster's bands exist as variables, just without
    a computed numeric range (that would require reading pixel data, out
    of scope for a lightweight registry write).

    Writes both dimension columns (lat/lon/time, is_dimension=True) and
    real data variables (is_dimension=False) — Phase 3's admin review
    needs to see the full detected shape, not just the data variables, to
    meaningfully assign Dimension/Filter roles.

    roles is deliberately left empty here — an unreviewed variable has no
    role until an admin sets one (Phase 3); this function's job ends at
    "detected and stored."
    """
    dataset_id = dataset_file.dataset_id
    written = 0

    lat_col = metadata.extra.get("lat_col")
    lon_col = metadata.extra.get("lon_col")
    time_col = metadata.extra.get("time_col")
    depth_col = metadata.extra.get("depth_col")

    if metadata.shape == DataShape.RASTER:
        for band_name in metadata.bands:
            _upsert_dataset_variable(
                db,
                dataset_id=dataset_id,
                name=band_name,
                data_type=VariableDataType.NUMERIC.value,
                is_dimension=False,
            )
            written += 1
        db.flush()
        return written

    # Tabular: register the detected coordinate/dimension columns first —
    # depth is registered as is_dimension=True exactly like lat/lon/time,
    # the same convention the Oceanographic Profiles module's dimension
    # detection (visualize_service.dataset_has_depth_dimension) relies on;
    # no new VariableRole was added for this since is_dimension + name
    # matching is sufficient without a schema migration.
    for col, dtype in (
        (lat_col, VariableDataType.NUMERIC),
        (lon_col, VariableDataType.NUMERIC),
        (time_col, VariableDataType.TEMPORAL),
        (depth_col, VariableDataType.NUMERIC),
    ):
        if not col:
            continue
        _upsert_dataset_variable(
            db, dataset_id=dataset_id, name=col, data_type=dtype.value, is_dimension=True
        )
        written += 1

    # ...then every real data variable, with a min/max range read cheaply
    # from Parquet's own row-group statistics (no full-column data read
    # needed for numeric columns) and a capped distinct-value sample for
    # non-numeric (categorical/text) columns, which _write_dataset_records
    # never turns into DatasetRecord rows at all today (its per-row loop
    # only accepts finite numeric values) — the registry still records
    # that these columns exist, even though they aren't yet queryable as
    # DatasetRecord rows.
    #
    # Zarr-backed artifacts (large NetCDF, Phase 2) have no Parquet file to
    # read stats from — register their real data variables directly from
    # metadata.variables instead, same as the raster band path above:
    # existence is recorded, numeric range is not (would require reading
    # chunk data, out of scope for this lightweight write).
    coord_cols = {c for c in (lat_col, lon_col, time_col, depth_col) if c}
    if artifact is not None and artifact.is_zarr:
        for var_name in metadata.variables:
            if var_name in coord_cols:
                continue
            _upsert_dataset_variable(
                db,
                dataset_id=dataset_id,
                name=var_name,
                data_type=VariableDataType.NUMERIC.value,
                is_dimension=False,
            )
            written += 1
    elif artifact is not None and artifact.local_path.exists():
        parquet_file = pq.ParquetFile(artifact.local_path)
        schema_names = set(parquet_file.schema_arrow.names)
        variable_columns = sorted(
            col
            for col in schema_names
            if col in metadata.variables
            and col not in coord_cols
            and not col.endswith(_NON_VARIABLE_SUFFIXES)
        )

        for col in variable_columns:
            field = parquet_file.schema_arrow.field(col)
            is_numeric = _pyarrow_type_is_numeric(field.type)
            data_type = VariableDataType.NUMERIC.value if is_numeric else VariableDataType.CATEGORICAL.value

            min_value = max_value = None
            distinct_values: list[str] | None = None

            if is_numeric:
                min_value, max_value = _numeric_range_from_row_groups(parquet_file, col)
            else:
                distinct_values = _sample_distinct_values(parquet_file, col)

            _upsert_dataset_variable(
                db,
                dataset_id=dataset_id,
                name=col,
                data_type=data_type,
                is_dimension=False,
                min_value=min_value,
                max_value=max_value,
                distinct_values=distinct_values,
            )
            written += 1

    db.flush()
    return written


def _pyarrow_type_is_numeric(pa_type) -> bool:
    import pyarrow as pa

    return pa.types.is_floating(pa_type) or pa.types.is_integer(pa_type)


def _numeric_range_from_row_groups(parquet_file, column: str) -> tuple[float | None, float | None]:
    """Reads min/max from Parquet's own row-group statistics — metadata
    already written into the file, no data read required. Falls back to
    (None, None) if a row group lacks statistics (e.g. an all-null column)
    rather than raising."""
    min_value = max_value = None
    for rg_index in range(parquet_file.num_row_groups):
        col_meta = parquet_file.metadata.row_group(rg_index).column(
            parquet_file.schema_arrow.get_field_index(column)
        )
        stats = col_meta.statistics
        if stats is None or not stats.has_min_max:
            continue
        rg_min, rg_max = float(stats.min), float(stats.max)
        min_value = rg_min if min_value is None else min(min_value, rg_min)
        max_value = rg_max if max_value is None else max(max_value, rg_max)
    return min_value, max_value


def _sample_distinct_values(parquet_file, column: str) -> list[str]:
    """Scans the file (in batches, never loading the whole column into
    memory at once) collecting distinct string values for `column`, up to
    _MAX_SAMPLED_DISTINCT_VALUES — a filter-dropdown data source, not an
    exhaustive enumeration, so scanning stops the moment the cap is hit
    rather than reading the rest of a TB-scale file just to discard it."""
    seen: set[str] = set()
    for record_batch in parquet_file.iter_batches(batch_size=_RECORD_BATCH_SIZE, columns=[column]):
        for value in record_batch.column(0):
            if len(seen) >= _MAX_SAMPLED_DISTINCT_VALUES:
                return sorted(seen)
            py_value = value.as_py()
            if py_value is not None:
                seen.add(str(py_value))
    return sorted(seen)
