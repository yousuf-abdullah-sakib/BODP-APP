import tempfile
from datetime import date
from pathlib import Path

import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.models.catalog import Dataset, DatasetFile
from app.models.uploads import QualityIssue, Upload, UploadStatus
from app.services.parsers import ParserError, get_parser_for_format
from app.services.parsers.base import DataShape
from app.services.storage.keys import processed_key
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _widen(current: date | None, candidate: date | None, *, take_min: bool) -> date | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return min(current, candidate) if take_min else max(current, candidate)


@celery_app.task(name="ingestion.process_dataset_file", bind=True, max_retries=2)
def process_dataset_file(self, dataset_file_id: str, upload_id: str | None = None) -> dict:
    """Runs after a raw file has been uploaded to storage (Master Plan §3
    Phase 2 task 4): parse it, auto-detect spatial/temporal extent and
    variables, write a query-optimized Parquet copy to the processed/ tier,
    and update dataset_files / datasets / uploads accordingly.

    Failure at any stage marks the upload 'failed' with a clear reason
    rather than leaving it stuck 'processing' or silently succeeding on bad
    data (Master Plan §3 Phase 2 quality check).
    """
    with get_sync_db() as db:
        dataset_file = db.get(DatasetFile, dataset_file_id)
        if dataset_file is None:
            logger.error("ingestion.dataset_file_not_found", dataset_file_id=dataset_file_id)
            return {"status": "failed", "reason": "dataset_file not found"}

        upload = db.get(Upload, upload_id) if upload_id else None
        if upload:
            upload.status = UploadStatus.PROCESSING.value
            upload.celery_task_id = self.request.id
            db.commit()

        try:
            result = _run_ingestion(db, dataset_file)
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


def _run_ingestion(db, dataset_file: DatasetFile) -> dict:
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

        metadata = parser.parse(raw_local_path)
        # Format-agnostic: tabular parsers write Parquet, GeoTIFF writes a
        # re-tiled COG — the ingestion task doesn't need to know which.
        artifact = parser.to_processed(raw_local_path, tmp_dir_path)

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

    db.commit()

    return {
        "shape": metadata.shape.value,
        "variables": content_names,
        "record_count": content_count,
        "processed_key": processed_object_key,
    }


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
