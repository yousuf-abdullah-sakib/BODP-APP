import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.models.catalog import Dataset, DatasetFile, StorageKind
from app.models.requests import AccessGrant, ExtractionStatus, SubsetExtraction
from app.services.extractors import ExtractorError, bundle_as_zip, get_extractor_for_format
from app.services.ingestion_service import verify_checksum
from app.services.storage.keys import extract_key
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="extraction.run_extraction", bind=True, max_retries=2)
def run_extraction(self, extraction_id: str) -> dict:
    """Filters a grant's dataset down to the requested scope and format,
    writing the result to the extracts/ storage tier (Master Plan §3 Phase
    5 task 2). Mirrors ingestion.process_dataset_file's status-transition
    and error-handling shape exactly — never lets an exception escape
    unhandled, always leaves the row in a terminal, explainable state.
    """
    with get_sync_db() as db:
        return process_extraction(db, extraction_id, celery_task_id=self.request.id)


def process_extraction(db, extraction_id: str, *, celery_task_id: str | None = None) -> dict:
    """Core status-transition + error-handling logic, factored out of the
    Celery task so extraction_service's small-file synchronous fast path
    (Master Plan §3 Phase 5 task 5) can run the exact same code in-process,
    on its own sync session, without going through the broker at all."""
    extraction = db.get(SubsetExtraction, extraction_id)
    if extraction is None:
        logger.error("extraction.not_found", extraction_id=extraction_id)
        return {"status": "failed", "reason": "extraction not found"}

    extraction.status = ExtractionStatus.PROCESSING.value
    if celery_task_id:
        extraction.celery_task_id = celery_task_id
    db.commit()

    try:
        result = _run_extraction(db, extraction)
    except ExtractorError as exc:
        logger.warning("extraction.failed", extraction_id=extraction_id, reason=str(exc))
        extraction.status = ExtractionStatus.FAILED.value
        extraction.error_message = str(exc)
        db.commit()
        return {"status": "failed", "reason": str(exc)}
    except Exception as exc:
        logger.exception("extraction.unexpected_error", extraction_id=extraction_id)
        extraction.status = ExtractionStatus.FAILED.value
        extraction.error_message = f"Unexpected error during extraction: {exc}"
        db.commit()
        return {"status": "failed", "reason": str(exc)}

    extraction.status = ExtractionStatus.COMPLETE.value
    extraction.output_storage_backend = result["storage_backend"]
    extraction.output_bucket = result["bucket"]
    extraction.output_file_key = result["key"]
    extraction.output_size_bytes = result["size_bytes"]
    extraction.completed_at = datetime.now(UTC)
    db.commit()

    return {"status": "complete", **result}


def _run_extraction(db, extraction: SubsetExtraction) -> dict:
    grant = db.get(AccessGrant, extraction.grant_id)
    if grant is None:
        raise ExtractorError("Grant not found")

    dataset = db.get(Dataset, grant.dataset_id)
    if dataset is None:
        raise ExtractorError("Dataset not found")

    files = db.execute(
        select(DatasetFile).where(DatasetFile.dataset_id == dataset.id).order_by(DatasetFile.uploaded_at)
    ).scalars().all()
    if not files:
        raise ExtractorError("Dataset has no source files to extract from")

    extractor = get_extractor_for_format(extraction.format or "csv")
    scope = extraction.requested_scope or {}

    # All of a dataset's files are uploaded through the same admin upload
    # flow, which always targets one configured storage backend — using the
    # first file's backend for both source reads and the output write is
    # therefore correct, not just convenient.
    output_storage_backend = files[0].storage_backend

    with tempfile.TemporaryDirectory(prefix="bodp_extract_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        results = []

        for i, dataset_file in enumerate(files):
            is_zarr_csv_or_parquet = (
                dataset_file.storage_kind == StorageKind.CHUNKED_ARRAY.value
                and extractor.output_format in ("csv", "parquet")
            )
            if is_zarr_csv_or_parquet:
                # PLAN.md Phase 5: a Zarr-backed file has no single
                # processed_key object (only a processed_prefix
                # multi-object layout) — _source_for_format can't resolve
                # a downloadable input_path for it the way it does for
                # PARQUET/RASTER files. Materialize the (already
                # scope-filtered) Zarr store into a flat Parquet file
                # first, then hand that to the SAME CsvExtractor/
                # ParquetExtractor every other tabular source uses — no
                # extractor-side duplication of scope_filter.py's logic.
                local_input = _materialize_zarr_to_parquet(dataset_file, tmp_dir_path / f"input_{i}.parquet", scope)
            else:
                storage = get_storage_backend(dataset_file.storage_backend)
                source_key, source_bucket = _source_for_format(dataset_file, extractor.output_format)
                body = storage.get(source_bucket, source_key)

                local_input = tmp_dir_path / f"input_{i}{Path(source_key).suffix}"
                with open(local_input, "wb") as f:
                    while chunk := body.read(1024 * 1024):
                        f.write(chunk)

                # Phase 10.5: catches silent object-storage corruption
                # before it's baked into a user-facing download.
                # DatasetFile.checksum is captured against the RAW
                # upload only (dataset_file_service.py) — _source_for_format
                # can instead return the PROCESSED Parquet key for a
                # csv/parquet extraction of a non-Zarr file, which would
                # never match that checksum and isn't the object it
                # protects. Only verify when source_key actually is the
                # raw object; skip otherwise (not a failure — simply
                # nothing to check here). None checksum means the file
                # predates checksum tracking — also not a failure.
                if (
                    dataset_file.checksum
                    and source_key == dataset_file.storage_key
                    and not verify_checksum(local_input, dataset_file.checksum)
                ):
                    raise ExtractorError(
                        "Stored file failed checksum verification — possible corruption in "
                        "object storage. Please contact an administrator."
                    )

            out_dir = tmp_dir_path / f"out_{i}"
            out_dir.mkdir(exist_ok=True)
            result = extractor.extract(input_path=local_input, output_dir=out_dir, scope=scope)
            results.append(result)

        final = results[0] if len(results) == 1 else bundle_as_zip(results, tmp_dir_path)

        storage = get_storage_backend(output_storage_backend)
        output_bucket = default_bucket_for(output_storage_backend)
        output_key = extract_key(grant.id, extraction.id, extension=final.file_extension)

        with open(final.local_path, "rb") as f:
            stored = storage.put(output_bucket, output_key, f, content_type=final.content_type)

        return {
            "storage_backend": output_storage_backend,
            "bucket": output_bucket,
            "key": output_key,
            "size_bytes": stored.size_bytes,
        }


def _source_for_format(dataset_file: DatasetFile, output_format: str) -> tuple[str, str]:
    """CSV/Parquet extraction reads the already-flattened processed
    Parquet (nothing lost for those formats); NetCDF/.mat extraction reads
    the RAW original file so N-D structure survives the filter — see the
    module docstrings on NetcdfExtractor/MatExtractor for why. Never
    called for a CHUNKED_ARRAY file requesting csv/parquet output — that
    case is routed to _materialize_zarr_to_parquet instead (see
    _run_extraction), since such a file has no single processed_key
    object to resolve here."""
    if output_format in ("csv", "parquet"):
        metadata = dataset_file.file_metadata or {}
        processed_key = metadata.get("processed_key")
        processed_bucket = metadata.get("processed_bucket")
        if processed_key and processed_bucket:
            return processed_key, processed_bucket
    return dataset_file.storage_key, dataset_file.storage_bucket


def _materialize_zarr_to_parquet(dataset_file: DatasetFile, output_path: Path, scope: dict) -> Path:
    """PLAN.md Phase 5: opens a CHUNKED_ARRAY file's Zarr store (same
    fsspec/s3fs chunk-range-read mechanism gridded_query_service.py's
    query functions already use — see open_zarr_dataset), applies the
    SAME scope filters NetcdfExtractor._filter uses (date range via
    .sel(), bbox via .where(), parameter selection), then flattens the
    result to a tidy DataFrame via to_dataframe() and writes it to a
    local Parquet file — which CsvExtractor/ParquetExtractor then read
    completely unmodified, exactly as they already do for a real
    PARQUET-backed file's processed object. Filtering BEFORE flattening
    (not after) is what keeps this from ever pulling an unfiltered whole
    grid into memory for a large source.

    Scope fields deliberately NOT applied here, and why (Data Page Filter
    & Extraction Audit, high #3 — stated explicitly rather than left as a
    silent gap, mirroring gridded_query_service.py's identical rationale
    for the live-preview path against the same CHUNKED_ARRAY files):
    - quality/source/platform/station/format/processing_level: per-
      observation tabular metadata columns that only exist because
      ingestion.py's _write_dataset_records adds them when building a
      tidy DatasetRecord row — a Zarr store has coordinates + data
      variables, never these columns, so there is nothing to filter
      against. Genuinely inapplicable, not merely unimplemented.
    - depth_min/depth_max: every CHUNKED_ARRAY file in this system today
      is (lat, lon, time)-indexed only — verified directly, no real Zarr
      dataset has a depth coordinate — so there is no depth axis to slice
      against. Would need to become a real .sel()-style filter (matching
      the date_from/date_to pattern above) the day a depth-resolved
      gridded dataset is actually ingested; silently doing nothing today
      is correct only because that axis genuinely doesn't exist yet.
    date_from/date_to, bounds, and parameters (applied below) are the
    only scope fields with a genuine per-cell analog for gridded data."""
    from app.services.gridded_query_service import open_zarr_dataset

    with open_zarr_dataset(dataset_file) as ds:
        lat_name = next((c for c in ("lat", "latitude", "y") if c in ds.coords), None)
        lon_name = next((c for c in ("lon", "longitude", "x") if c in ds.coords), None)
        time_name = "time" if "time" in ds.coords else None

        if time_name is not None and (scope.get("date_from") or scope.get("date_to")):
            lo = scope["date_from"] if scope.get("date_from") else ds[time_name].min().values
            hi = scope["date_to"] if scope.get("date_to") else ds[time_name].max().values
            ds = ds.sel({time_name: slice(lo, hi)})

        bounds = scope.get("bounds")
        if bounds:
            mask = None
            if lat_name is not None:
                lat_mask = (ds[lat_name] >= bounds["lat_min"]) & (ds[lat_name] <= bounds["lat_max"])
                mask = lat_mask if mask is None else mask & lat_mask
            if lon_name is not None:
                lon_mask = (ds[lon_name] >= bounds["lon_min"]) & (ds[lon_name] <= bounds["lon_max"])
                mask = lon_mask if mask is None else mask & lon_mask
            if mask is not None:
                # .compute() first — xarray refuses boolean indexing with
                # a dask-backed (lazy-chunked) mask against .where(...,
                # drop=True); see gridded_query_service._apply_bbox's
                # identical fix, found via the same live large-Zarr-file
                # verification.
                ds = ds.where(mask.compute(), drop=True)

        parameters = scope.get("parameters") or ([scope["parameter"]] if scope.get("parameter") else None)
        if parameters:
            lowered = {str(p).lower() for p in parameters}
            matching = [v for v in ds.data_vars if str(v).lower() in lowered]
            if matching:
                ds = ds[matching]

        df = ds.to_dataframe().reset_index()

    df.to_parquet(output_path, index=False)
    return output_path
