import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.models.catalog import Dataset, DatasetFile
from app.models.requests import AccessGrant, ExtractionStatus, SubsetExtraction
from app.services.extractors import ExtractorError, bundle_as_zip, get_extractor_for_format
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
            storage = get_storage_backend(dataset_file.storage_backend)
            source_key, source_bucket = _source_for_format(dataset_file, extractor.output_format)
            body = storage.get(source_bucket, source_key)

            local_input = tmp_dir_path / f"input_{i}{Path(source_key).suffix}"
            with open(local_input, "wb") as f:
                while chunk := body.read(1024 * 1024):
                    f.write(chunk)

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
    module docstrings on NetcdfExtractor/MatExtractor for why."""
    if output_format in ("csv", "parquet"):
        metadata = dataset_file.file_metadata or {}
        processed_key = metadata.get("processed_key")
        processed_bucket = metadata.get("processed_bucket")
        if processed_key and processed_bucket:
            return processed_key, processed_bucket
    return dataset_file.storage_key, dataset_file.storage_bucket
