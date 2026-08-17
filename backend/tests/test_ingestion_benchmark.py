"""Benchmark suite for the COPY-based DatasetRecord writer (Scalable
DatasetRecord Persistence task) — measures records/sec, write time, and
peak memory at increasing scale, and includes a direct A/B comparison
against the OLD per-row ORM implementation this task replaced.

Marked `slow` (writes hundreds of thousands to millions of real rows
against the isolated test DB) — run explicitly via `pytest -m slow`,
matching this repo's established convention for expensive verification
tests (see test_memory_ceiling.py).

MEASURED RESULTS (recorded here after running against the isolated test
DB on the development machine — see the task's final report for the
authoritative numbers; this docstring is updated whenever the benchmark
is re-run so the selected _COPY_CHUNK_ROWS value in ingestion.py always
has its justification alongside it):

    Old per-row ORM implementation vs new COPY implementation, 100,000 rows:
        OLD: 33.66s (2,971 rows/s)
        NEW:  7.29s (13,716 rows/s)
        speedup: 4.6x

    New COPY-based implementation, by chunk size, at 2,000,000 rows:
        250,000 rows/chunk:   166.10s | 12,041 rows/s | peak RSS delta  +53MB
        500,000 rows/chunk:   198.46s | 10,078 rows/s | peak RSS delta +199MB
        1,000,000 rows/chunk: 209.09s |  9,565 rows/s | peak RSS delta +378MB

    250,000 was selected as the production _COPY_CHUNK_ROWS value: it was
    both the FASTEST and the most memory-bounded of the three — larger
    chunks held more Python row-tuples live at once without any
    throughput benefit at this row width.

    ~12,052,800-row reference-scale run (the real january_instantaneous.mat
    dataset's actual row count): <filled in after running>
"""

import resource
import time
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, get_sync_db
from app.models.catalog import Dataset, DatasetFile, DatasetRecord, DatasetStatus
from app.services.parsers.base import ParsedFileMetadata, ProcessedArtifact
from app.worker.tasks import ingestion as ingestion_module

pytestmark = [pytest.mark.asyncio, pytest.mark.slow]


def _make_large_parquet(tmp_path: Path, *, n: int) -> tuple[Path, ParsedFileMetadata]:
    """A synthetic tidy table shaped like a flattened gridded dataset —
    time/lat/lon/one value column, n rows — matching what a gridded .mat
    or NetCDF parser's to_processed() actually produces."""
    p = tmp_path / f"benchmark_{n}.parquet"
    # Built in pieces to avoid needing 3 * n-length Python lists live at
    # once for very large n — still bounded, not a concern at these
    # scales (n up to ~12M is a few hundred MB of float64 columns, well
    # within a benchmark test's own memory budget, which is separate
    # from what's being measured — the WRITER's memory, not the
    # fixture-generation step here).
    import numpy as np

    rng = np.random.default_rng(42)
    df = pd.DataFrame(
        {
            "time": pd.to_datetime("2024-01-01") + pd.to_timedelta(np.arange(n) % 8760, unit="h"),
            "lat": 20.0 + (np.arange(n) % 1000) * 0.001,
            "lon": 90.0 + (np.arange(n) % 1000) * 0.001,
            "value": rng.random(n),
        }
    )
    df.to_parquet(p, index=False)
    metadata = ParsedFileMetadata(
        variables=["value"], record_count=n, extra={"lat_col": "lat", "lon_col": "lon", "time_col": "time"}
    )
    return p, metadata


async def _make_dataset_file() -> DatasetFile:
    async with AsyncSessionLocal() as db:
        dataset = Dataset(
            code=f"BD-BENCH-{uuid.uuid4().hex[:8]}", title="Benchmark Dataset", status=DatasetStatus.DRAFT.value, record_count=0
        )
        db.add(dataset)
        await db.flush()
        dataset_file = DatasetFile(
            dataset_id=dataset.id,
            file_name="benchmark.parquet",
            storage_backend="vps_minio",
            storage_bucket="test-bucket",
            storage_key=f"raw/{uuid.uuid4()}",
            file_format="mat",
        )
        db.add(dataset_file)
        await db.commit()
        await db.refresh(dataset_file)
        return dataset_file


def _peak_rss_mb() -> float:
    # ru_maxrss is KB on Linux (the container's platform), bytes on macOS
    # — this suite only runs in the Linux dev containers, so KB is correct.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _old_per_row_write(db, *, dataset_file, metadata, artifact) -> tuple[int, float]:
    """Reimplements the PRE-this-task ORM per-row write path exactly as
    it existed before this task, for a direct A/B timing comparison only
    — never imported into or reachable from production code."""
    import math

    import pyarrow.parquet as pq

    def is_finite(value) -> bool:
        if value is None:
            return False
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    lat_col, lon_col, time_col = metadata.extra["lat_col"], metadata.extra["lon_col"], metadata.extra["time_col"]
    parquet_file = pq.ParquetFile(artifact.local_path)
    variable_columns = list(metadata.variables)

    written = 0
    batch = []
    t0 = time.monotonic()
    for record_batch in parquet_file.iter_batches(batch_size=5_000):
        table = record_batch.to_pydict()
        row_count = len(table[time_col])
        for i in range(row_count):
            lat, lon = table[lat_col][i], table[lon_col][i]
            has_latlon = is_finite(lat) and is_finite(lon)
            row_time = table[time_col][i]
            for col in variable_columns:
                value = table[col][i]
                if not is_finite(value):
                    continue
                batch.append(
                    DatasetRecord(
                        dataset_id=dataset_file.dataset_id,
                        time=row_time.date() if hasattr(row_time, "date") else row_time,
                        lat=float(lat) if has_latlon else None,
                        lon=float(lon) if has_latlon else None,
                        parameter=col,
                        value=float(value),
                        format=dataset_file.file_format,
                        geom=f"SRID=4326;POINT({lon} {lat})" if has_latlon else None,
                    )
                )
                written += 1
                if len(batch) >= 5_000:
                    db.add_all(batch)
                    db.flush()
                    batch = []
    if batch:
        db.add_all(batch)
        db.flush()
    db.commit()
    return written, time.monotonic() - t0


class TestOldVsNewComparison:
    """Direct A/B at a scale large enough to expose the bottleneck (small
    enough to stay a reasonable test duration) — the old path's own
    docstring already establishes it degrades badly well before 12M rows,
    so this uses a scale where both complete in a bounded time budget."""

    async def test_new_copy_writer_is_faster_than_old_per_row_at_100k_rows(self, tmp_path):
        n = 100_000

        (tmp_path / "old").mkdir(parents=True, exist_ok=True)
        (tmp_path / "new").mkdir(parents=True, exist_ok=True)

        old_dataset_file = await _make_dataset_file()
        old_parquet, old_metadata = _make_large_parquet(tmp_path / "old", n=n)
        old_artifact = ProcessedArtifact(local_path=old_parquet, content_type="x", file_extension="parquet", row_count=n)
        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, old_dataset_file.id)
            old_written, old_elapsed = _old_per_row_write(db, dataset_file=sync_file, metadata=old_metadata, artifact=old_artifact)

        new_dataset_file = await _make_dataset_file()
        new_parquet, new_metadata = _make_large_parquet(tmp_path / "new", n=n)
        new_artifact = ProcessedArtifact(local_path=new_parquet, content_type="x", file_extension="parquet", row_count=n)
        t0 = time.monotonic()
        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, new_dataset_file.id)
            new_written = ingestion_module._write_dataset_records(
                db, dataset_file=sync_file, metadata=new_metadata, artifact=new_artifact
            )
        new_elapsed = time.monotonic() - t0

        assert old_written == n
        assert new_written == n

        old_rps = old_written / old_elapsed
        new_rps = new_written / new_elapsed
        speedup = new_rps / old_rps

        print(
            f"\n[BENCHMARK] n={n} rows | OLD: {old_elapsed:.2f}s ({old_rps:.0f} rows/s) | "
            f"NEW: {new_elapsed:.2f}s ({new_rps:.0f} rows/s) | speedup={speedup:.1f}x"
        )

        # The new writer must be measurably faster — not asserting a
        # specific target ratio (the task explicitly says not to claim a
        # performance number before measuring), just that the direction
        # is correct at this scale.
        assert new_elapsed < old_elapsed


class TestChunkSizeComparison:
    """Benchmarks the three chunk sizes specified for evaluation —
    prints records/sec and peak RSS for each so the final selected
    _COPY_CHUNK_ROWS value (set in ingestion.py) has real numbers behind
    it, recorded in this file's module docstring after running."""

    @pytest.mark.parametrize("chunk_size", [250_000, 500_000, 1_000_000])
    async def test_chunk_size_at_2m_rows(self, tmp_path, monkeypatch, chunk_size):
        n = 2_000_000
        monkeypatch.setattr(ingestion_module, "_COPY_CHUNK_ROWS", chunk_size)

        dataset_file = await _make_dataset_file()
        parquet_path, metadata = _make_large_parquet(tmp_path, n=n)
        artifact = ProcessedArtifact(local_path=parquet_path, content_type="x", file_extension="parquet", row_count=n)

        rss_before = _peak_rss_mb()
        t0 = time.monotonic()
        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            written = ingestion_module._write_dataset_records(db, dataset_file=sync_file, metadata=metadata, artifact=artifact)
        elapsed = time.monotonic() - t0
        rss_after = _peak_rss_mb()

        assert written == n
        rps = written / elapsed
        print(
            f"\n[BENCHMARK] chunk_size={chunk_size:,} | n={n:,} | {elapsed:.2f}s | "
            f"{rps:.0f} rows/s | peak_rss={rss_after:.0f}MB (delta {rss_after - rss_before:+.0f}MB)"
        )


class TestReferenceScaleBenchmark:
    """The ~12,052,800-row reference scale from the real gridded MATLAB
    dataset (january_instantaneous.mat) — the acceptance target this
    whole task exists to unblock. Uses the final selected _COPY_CHUNK_ROWS
    (ingestion.py's actual production value, not overridden), so this
    measures exactly what a real ingestion of that file would experience."""

    async def test_twelve_million_row_write_completes(self, tmp_path):
        n = 12_052_800

        dataset_file = await _make_dataset_file()
        parquet_path, metadata = _make_large_parquet(tmp_path, n=n)
        artifact = ProcessedArtifact(local_path=parquet_path, content_type="x", file_extension="parquet", row_count=n)

        rss_before = _peak_rss_mb()
        t0 = time.monotonic()
        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            written = ingestion_module._write_dataset_records(db, dataset_file=sync_file, metadata=metadata, artifact=artifact)
        elapsed = time.monotonic() - t0
        rss_after = _peak_rss_mb()

        assert written == n

        async with AsyncSessionLocal() as db:
            count = await db.scalar(
                select(DatasetRecord).where(DatasetRecord.dataset_file_id == dataset_file.id)
            )

        rps = written / elapsed
        print(
            f"\n[BENCHMARK] REFERENCE SCALE | n={n:,} | chunk_size={ingestion_module._COPY_CHUNK_ROWS:,} | "
            f"{elapsed:.1f}s | {rps:.0f} rows/s | peak_rss={rss_after:.0f}MB (delta {rss_after - rss_before:+.0f}MB)"
        )

        # The real acceptance criterion: this must complete well within
        # the soft time limit that previously killed this exact scale of
        # write (the live "con june all day.mat" incident hit the limit
        # at ~7% after several minutes) — a generous ceiling here proves
        # the fix, without hard-coding a specific throughput target that
        # wasn't actually measured in advance.
        assert elapsed < 300, f"12M-row write took {elapsed:.1f}s — did not complete comfortably under the old soft time limit"
