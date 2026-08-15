import math
import tempfile
from datetime import date, datetime
from pathlib import Path

import pyarrow.parquet as pq
import structlog
from celery.exceptions import SoftTimeLimitExceeded
from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select

from app.core.database import get_sync_db
from app.models.catalog import Dataset, DatasetFile, DatasetRecord, DatasetVariable, VariableDataType
from app.models.uploads import QualityIssue, Upload, UploadStatus
from app.services.parsers import ParserError, get_parser_for_format
from app.services.parsers.base import DataShape, ParsedFileMetadata, ProcessedArtifact
from app.services.storage.keys import processed_key
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Non-variable columns every tidy Parquet artifact may carry alongside the
# actual observation values — never turned into their own DatasetRecord rows.
_NON_VARIABLE_SUFFIXES = ("_bnds", "_bounds")

_RECORD_BATCH_SIZE = 5_000

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
    (which is only set — and only committed — after DatasetRecord writes
    succeed, i.e. after the point cancellation can still interrupt)."""

    def __init__(self, *, processed_bucket: str | None = None, processed_key: str | None = None):
        self.processed_bucket = processed_bucket
        self.processed_key = processed_key
        super().__init__("Ingestion cancelled")


def _checkpoint(
    db,
    upload: Upload | None,
    *,
    written_processed_bucket: str | None = None,
    written_processed_key: str | None = None,
) -> None:
    """Re-reads Upload.status from the DB and raises IngestionCancelled if
    it's been flipped to CANCELLED since this task started. celery's own
    task revocation (control.revoke(terminate=True)) is a SIGTERM that
    cannot interrupt a blocking library call already in progress (e.g.
    mid xr.open_dataset() read) — this cooperative checkpoint, called
    between discrete steps, is the actual mechanism that makes
    cancellation during processing take effect promptly and safely rather
    than relying solely on the signal."""
    if upload is None:
        return
    db.refresh(upload)
    if upload.status == UploadStatus.CANCELLED.value:
        raise IngestionCancelled(
            processed_bucket=written_processed_bucket, processed_key=written_processed_key
        )


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
            _cleanup_cancelled_ingestion(
                db,
                dataset_file,
                processed_bucket=exc.processed_bucket,
                processed_key=exc.processed_key,
            )
            if upload:
                upload.status = UploadStatus.CANCELLED.value
                upload.error_message = "Cancelled during processing."
                db.commit()
            return {"status": "cancelled"}
        except ParserError as exc:
            logger.warning(
                "ingestion.parse_rejected",
                dataset_file_id=dataset_file_id,
                reason=str(exc),
            )
            if upload:
                upload.status = UploadStatus.FAILED.value
                upload.error_message = str(exc)
                db.commit()
            return {"status": "failed", "reason": str(exc)}
        except SoftTimeLimitExceeded:
            # Phase 2: the per-dispatch soft time limit (celery_app.py's
            # ingestion_soft_time_limit_seconds, computed from the file's
            # actual size) was exceeded — a clear, specific reason rather
            # than falling through to the generic "Unexpected error"
            # message below. Whatever this run had already written to the
            # DB is cleaned up the same way an explicit cancellation is
            # (db.rollback() + delete the DatasetFile row) — from the
            # data's perspective it's the identical situation: ingestion
            # did not finish, nothing partial should be left behind as if
            # it had. Unlike a checkpoint-detected cancellation, a soft
            # time limit can fire at any point, not only at a known
            # checkpoint boundary, so this cannot reliably identify (and
            # therefore cannot delete) a processed/ object that might have
            # just been written — a rare, documented gap versus a
            # deliberate cancellation's precise cleanup.
            logger.warning("ingestion.soft_time_limit_exceeded", dataset_file_id=dataset_file_id)
            _cleanup_cancelled_ingestion(db, dataset_file, processed_bucket=None, processed_key=None)
            if upload:
                upload.status = UploadStatus.FAILED.value
                upload.error_message = (
                    "Processing exceeded the time limit for a file of this size. "
                    "This may indicate an unusually large or complex file — contact an administrator "
                    "if this persists."
                )
                db.commit()
            return {"status": "failed", "reason": "soft_time_limit_exceeded"}
        except Exception as exc:
            logger.exception("ingestion.unexpected_error", dataset_file_id=dataset_file_id)
            if upload:
                upload.status = UploadStatus.FAILED.value
                upload.error_message = f"Unexpected error during processing: {exc}"
                db.commit()
            return {"status": "failed", "reason": str(exc)}

        if upload:
            upload.status = UploadStatus.COMPLETE.value
            upload.dataset_file_id = dataset_file.id
            db.commit()

            # Mirrors the prototype's auto-generated QC entry on successful
            # upload completion (Master Plan frontend audit, DataUploadSection).
            if result.get("shape") == DataShape.RASTER.value:
                count_desc = f"{result['record_count']} pixels"
                names_desc = "band"
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

        return {"status": "complete", **result}


def _run_ingestion(db, dataset_file: DatasetFile, *, upload: Upload | None = None) -> dict:
    extension = (dataset_file.file_format or "").lower().lstrip(".")
    parser = get_parser_for_format(extension)

    storage = get_storage_backend(dataset_file.storage_backend)

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
        _checkpoint(db, upload)

        metadata = parser.parse(raw_local_path)
        # Format-agnostic: tabular parsers write Parquet, GeoTIFF writes a
        # re-tiled COG — the ingestion task doesn't need to know which.
        artifact = parser.to_processed(raw_local_path, tmp_dir_path)

        # Checkpoint 2: after conversion, before uploading the processed
        # artifact and writing DatasetRecord rows — the two steps most
        # worth skipping if cancellation arrived while conversion (the
        # slowest step for a large NetCDF/GeoTIFF) was running.
        _checkpoint(db, upload)

        processed_object_key = processed_key(
            dataset_file.dataset_id, dataset_file.id, extension=artifact.file_extension
        )
        processed_bucket = default_bucket_for(dataset_file.storage_backend)
        with open(artifact.local_path, "rb") as f:
            storage.put(
                processed_bucket,
                processed_object_key,
                f,
                content_type=artifact.content_type,
            )

        # Checkpoint 3: after the processed artifact is durably in storage,
        # before writing DatasetRecord rows to Postgres — if cancelled here,
        # _cleanup_cancelled_ingestion (called by the caller's except
        # IngestionCancelled handler) knows to delete this processed object
        # since dataset_file.file_metadata (set below) hasn't been
        # committed yet to point at it.
        _checkpoint(
            db,
            upload,
            written_processed_bucket=processed_bucket,
            written_processed_key=processed_object_key,
        )

        # Must happen before the TemporaryDirectory context exits below —
        # artifact.local_path (the Parquet file we just wrote and uploaded,
        # for tabular formats) is deleted along with the whole tmp_dir the
        # moment this `with` block ends, so this is the last point it's
        # still readable.
        #
        # Zarr-backed artifacts (large multidimensional NetCDF, Phase 2)
        # are excluded from both the Parquet-reading functions below —
        # _write_dataset_records/_write_variable_registry's per-variable
        # stats both read artifact.local_path as a Parquet file, which a
        # .zarr.zip is not. Same reasoning as why raster/GeoTIFF already
        # skips _write_dataset_records: a Zarr-backed dataset has no tidy-
        # table row concept to melt into DatasetRecord rows at all; its
        # data stays queryable through the Zarr store itself, not via
        # per-row Postgres records.
        records_written = 0
        if metadata.shape == DataShape.TABULAR and not artifact.is_zarr:
            records_written = _write_dataset_records(
                db, dataset_file=dataset_file, metadata=metadata, artifact=artifact
            )

        # Runs for every format, including raster and Zarr-backed NetCDF —
        # the schema registry records a GeoTIFF's bands (and a Zarr
        # dataset's variables/dimensions, from ParsedFileMetadata alone,
        # same as raster) even when no DatasetRecord rows were written.
        variables_registered = _write_variable_registry(
            db, dataset_file=dataset_file, metadata=metadata, artifact=artifact
        )

    dataset_file.spatial_extent = _bbox_wkt(metadata) if _has_bbox(metadata) else None
    dataset_file.temporal_start = metadata.temporal_start
    dataset_file.temporal_end = metadata.temporal_end

    # variables/record_count are the tabular-shaped view of "what does this
    # file contain" — for a raster, bands stand in for variables and pixel
    # dimensions stand in for a row count. Both get folded into the same
    # dataset.parameters/record_count aggregates below so catalog search
    # (Phase 3) doesn't need to know a file's shape to summarize a dataset.
    content_names = metadata.variables if metadata.shape == DataShape.TABULAR else metadata.bands
    content_count = (
        metadata.record_count
        if metadata.shape == DataShape.TABULAR
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
        "processed_bucket": processed_bucket,
        **{k: v for k, v in metadata.extra.items()},
    }

    dataset = db.get(Dataset, dataset_file.dataset_id)
    if dataset is not None:
        dataset.temporal_start = _widen(dataset.temporal_start, metadata.temporal_start, take_min=True)
        dataset.temporal_end = _widen(dataset.temporal_end, metadata.temporal_end, take_min=False)
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
        "variables": content_names,
        "record_count": content_count,
        "processed_key": processed_object_key,
        "dataset_records_written": records_written,
        "dataset_variables_registered": variables_registered,
    }


def _cleanup_cancelled_ingestion(
    db,
    dataset_file: DatasetFile,
    *,
    processed_bucket: str | None,
    processed_key: str | None,
) -> None:
    """Undoes everything a cancelled-mid-run _run_ingestion may have
    already written, so a cancelled upload never leaves an orphaned
    dataset/file behind (Phase 1 cancellation requirement):

    - Any processed/ artifact this run already uploaded (checkpoint 3
      onward) is deleted from storage — dataset_file.file_metadata was
      never committed to point at it (the commit happens after the
      checkpoint that would have caught the cancellation), so nothing else
      in the app knows this object exists; without this it would be a
      silent orphan in the processed/ prefix forever.
    - Any DatasetRecord rows this run already added via db.add_all() are
      discarded by db.rollback() below rather than committed — the
      _write_dataset_records batches were flushed (visible within this
      transaction) but never committed, so a rollback fully undoes them.
    - The DatasetFile row itself (created before ingestion started, when
      the raw upload completed) is deleted — a cancelled upload must not
      remain as an orphaned Dataset/DatasetFile shell. The raw object in
      raw/ is deliberately NOT deleted here: it's the original uploaded
      file, and per the storage-preservation guarantee raw files are only
      ever removed via the explicit "permanently delete dataset" admin
      action — an ingestion cancellation is not that.
    """
    db.rollback()

    if processed_bucket and processed_key:
        storage = get_storage_backend(dataset_file.storage_backend)
        storage.delete(processed_bucket, processed_key)

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
    db, *, dataset_file: DatasetFile, metadata: ParsedFileMetadata, artifact: ProcessedArtifact
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
    """
    lat_col = metadata.extra.get("lat_col")
    lon_col = metadata.extra.get("lon_col")
    time_col = metadata.extra.get("time_col")
    if not (time_col or (lat_col and lon_col)):
        logger.info(
            "ingestion.dataset_records_skipped_no_dimensions",
            dataset_file_id=str(dataset_file.id),
            reason="file has no detected time column and no detected lat+lon pair",
        )
        return 0

    parquet_file = pq.ParquetFile(artifact.local_path)
    all_columns = set(parquet_file.schema_arrow.names)
    coord_cols = {c for c in (lat_col, lon_col, time_col) if c}

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

    written = 0
    batch: list[DatasetRecord] = []

    def flush():
        nonlocal batch
        if batch:
            db.add_all(batch)
            db.flush()
            batch = []

    for record_batch in parquet_file.iter_batches(batch_size=_RECORD_BATCH_SIZE):
        table = record_batch.to_pydict()
        row_count = len(table[time_col] if time_col else table[lat_col])
        for i in range(row_count):
            lat = table[lat_col][i] if lat_col else None
            lon = table[lon_col][i] if lon_col else None
            has_latlon = _is_finite_number(lat) and _is_finite_number(lon)
            lat = float(lat) if has_latlon else None
            lon = float(lon) if has_latlon else None

            row_time = _resolve_row_time(table[time_col][i]) if time_col else None

            if row_time is None and not has_latlon:
                # Neither dimension resolved for this row (e.g. a null/
                # unparseable cell in an otherwise-present column) — this
                # specific row has no way to be located in time or space,
                # so it's skipped; other rows in the same file are
                # unaffected.
                continue

            for col in variable_columns:
                value = table[col][i]
                if not _is_finite_number(value):
                    continue
                batch.append(
                    DatasetRecord(
                        dataset_id=dataset_file.dataset_id,
                        time=row_time,
                        lat=lat,
                        lon=lon,
                        parameter=col,
                        value=float(value),
                        format=dataset_file.file_format,
                        geom=f"SRID=4326;POINT({lon} {lat})" if has_latlon else None,
                    )
                )
                written += 1
                if len(batch) >= _RECORD_BATCH_SIZE:
                    flush()

    flush()
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

    # Tabular: register the detected coordinate/dimension columns first...
    for col, dtype in ((lat_col, VariableDataType.NUMERIC), (lon_col, VariableDataType.NUMERIC), (time_col, VariableDataType.TEMPORAL)):
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
    coord_cols = {c for c in (lat_col, lon_col, time_col) if c}
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
