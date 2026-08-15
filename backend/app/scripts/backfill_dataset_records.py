"""Backfills DatasetRecord rows for DatasetFile rows that were already
successfully ingested (file_metadata is set — parse()/to_processed() both
completed and the processed artifact was uploaded) but never got their
DatasetRecord rows written, because the real ingestion pipeline
(app.worker.tasks.ingestion._run_ingestion) didn't insert them before this
fix — it only updated dataset.record_count as a summary counter.

This is a one-time data-repair tool, not part of the regular ingestion
path. It does NOT re-run parsing from scratch: it re-parses the raw file
(needed to recover the tidy Parquet shape / lat-lon-time column names)
using the exact same parser the original ingestion used, then reuses
_write_dataset_records — the same function future uploads now call inline.

Only touches DatasetFile rows that:
  - have file_metadata set (ingestion genuinely completed), and
  - are tabular (never raster/GeoTIFF — pixel data is never turned into
    per-pixel DatasetRecord rows, by design), and
  - currently have zero DatasetRecord rows for their dataset.

Usage:
    python -m app.scripts.backfill_dataset_records [--dataset-id ID] [--yes]

Without --yes, prints what would be backfilled and exits without writing
anything (dry run). --dataset-id scopes the backfill to one dataset;
omitted, it considers every eligible DatasetFile in the database.
"""

import argparse
import asyncio
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal, get_sync_db
from app.models.catalog import Dataset, DatasetFile, DatasetRecord
from app.services.parsers import get_parser_for_format
from app.services.parsers.base import DataShape
from app.services.storage.registry import get_storage_backend
from app.worker.tasks.ingestion import _write_dataset_records


async def _find_eligible_dataset_files(dataset_id: uuid.UUID | None) -> list[uuid.UUID]:
    async with AsyncSessionLocal() as db:
        stmt = select(DatasetFile.id).where(DatasetFile.file_metadata.isnot(None))
        if dataset_id is not None:
            stmt = stmt.where(DatasetFile.dataset_id == dataset_id)
        all_files = (await db.execute(stmt)).scalars().all()

        eligible = []
        for file_id in all_files:
            dataset_file = await db.get(DatasetFile, file_id)
            if dataset_file.file_metadata.get("shape") == DataShape.RASTER.value:
                continue  # rasters never get DatasetRecord rows, by design
            existing = await db.scalar(
                select(func.count(DatasetRecord.id)).where(
                    DatasetRecord.dataset_id == dataset_file.dataset_id
                )
            )
            # A dataset with ANY existing records is left alone — this tool
            # backfills datasets stuck at zero, it doesn't try to guess
            # whether a partially-populated dataset needs more rows.
            if existing == 0:
                eligible.append(file_id)
        return eligible


def _backfill_one(dataset_file_id: uuid.UUID) -> int:
    """Runs synchronously (matching ingestion.py's own sync-DB convention
    for anything touching storage/parsing) — re-downloads the raw file and
    re-parses it exactly as the original ingestion did."""
    with get_sync_db() as db:
        dataset_file = db.get(DatasetFile, dataset_file_id)
        if dataset_file is None:
            return 0

        extension = (dataset_file.file_format or "").lower().lstrip(".")
        parser = get_parser_for_format(extension)
        storage = get_storage_backend(dataset_file.storage_backend)

        with tempfile.TemporaryDirectory(prefix="bodp_backfill_") as tmp_dir:
            tmp_dir_path = Path(tmp_dir)
            raw_local_path = tmp_dir_path / "raw_input"

            body = storage.get(dataset_file.storage_bucket, dataset_file.storage_key)
            with open(raw_local_path, "wb") as f:
                while chunk := body.read(1024 * 1024):
                    f.write(chunk)

            metadata = parser.parse(raw_local_path)
            if metadata.shape != DataShape.TABULAR:
                return 0
            artifact = parser.to_processed(raw_local_path, tmp_dir_path)

            written = _write_dataset_records(
                db, dataset_file=dataset_file, metadata=metadata, artifact=artifact
            )

        db.commit()
        return written


async def backfill(*, dataset_id: uuid.UUID | None = None, dry_run: bool = True) -> dict:
    eligible_ids = await _find_eligible_dataset_files(dataset_id)

    if dry_run:
        async with AsyncSessionLocal() as db:
            details = []
            for file_id in eligible_ids:
                f = await db.get(DatasetFile, file_id)
                dataset = await db.get(Dataset, f.dataset_id)
                details.append(
                    {
                        "dataset_file_id": str(f.id),
                        "dataset_id": str(f.dataset_id),
                        "dataset_title": dataset.title if dataset else None,
                        "file_name": f.file_name,
                    }
                )
        return {"dry_run": True, "eligible_count": len(eligible_ids), "files": details}

    results = {}
    for file_id in eligible_ids:
        written = await asyncio.to_thread(_backfill_one, file_id)
        results[str(file_id)] = written
    return {"dry_run": False, "eligible_count": len(eligible_ids), "records_written": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", type=str, default=None)
    parser.add_argument("--yes", action="store_true", help="Actually write records (default is dry run)")
    args = parser.parse_args()

    result = asyncio.run(
        backfill(
            dataset_id=uuid.UUID(args.dataset_id) if args.dataset_id else None,
            dry_run=not args.yes,
        )
    )
    print(result)
