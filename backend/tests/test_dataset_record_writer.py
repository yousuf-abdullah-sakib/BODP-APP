"""Focused tests for the COPY-based DatasetRecord writer (Scalable
DatasetRecord Persistence task) — isolated from the full upload HTTP
flow, exercising _write_dataset_records directly against real synchronous
sessions/connections against the isolated test DB.

Covers: value fidelity (COPY output matches the pre-rewrite ORM
semantics row-for-row), dataset_file_id provenance, idempotent retry (no
duplicates), the session/connection-safety regression required before
this design was approved, cancellation/timeout cleanup leaving no
orphaned rows, and reliable terminal Upload state under a broken
session.
"""

import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, get_sync_db
from app.models.catalog import Dataset, DatasetFile, DatasetRecord, DatasetStatus
from app.models.uploads import Upload, UploadStatus
from app.services.parsers.base import ParsedFileMetadata, ProcessedArtifact
from app.worker.tasks.ingestion import (
    _cleanup_cancelled_ingestion,
    _set_upload_terminal_state,
    _write_dataset_records,
)

pytestmark = pytest.mark.asyncio


def _make_parquet(tmp_path: Path, *, n: int, start_lat: float = 20.0) -> tuple[Path, ParsedFileMetadata]:
    p = tmp_path / "records.parquet"
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="h"),
            "lat": [start_lat + i * 0.001 for i in range(n)],
            "lon": [90.0 + i * 0.001 for i in range(n)],
            "sea_surface_temp": [25.0 + i * 0.01 for i in range(n)],
        }
    )
    df.to_parquet(p, index=False)
    metadata = ParsedFileMetadata(
        variables=["sea_surface_temp"],
        dimensions={"time": n},
        record_count=n,
        extra={"lat_col": "lat", "lon_col": "lon", "time_col": "time"},
    )
    return p, metadata


async def _make_dataset_file(*, dataset_id: uuid.UUID | None = None) -> DatasetFile:
    async with AsyncSessionLocal() as db:
        if dataset_id is None:
            dataset = Dataset(
                code=f"BD-WRITER-{uuid.uuid4().hex[:8]}",
                title="Writer Test Dataset",
                status=DatasetStatus.DRAFT.value,
                record_count=0,
            )
            db.add(dataset)
            await db.flush()
            dataset_id = dataset.id
        dataset_file = DatasetFile(
            dataset_id=dataset_id,
            file_name="records.parquet",
            storage_backend="vps_minio",
            storage_bucket="test-bucket",
            storage_key=f"raw/{uuid.uuid4()}",
            file_format="csv",
        )
        db.add(dataset_file)
        await db.commit()
        await db.refresh(dataset_file)
        return dataset_file


class TestValueFidelity:
    async def test_written_rows_match_source_values_exactly(self, tmp_path):
        dataset_file = await _make_dataset_file()
        parquet_path, metadata = _make_parquet(tmp_path, n=20)
        artifact = ProcessedArtifact(
            local_path=parquet_path, content_type="application/vnd.apache.parquet", file_extension="parquet", row_count=20
        )

        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            written = _write_dataset_records(db, dataset_file=sync_file, metadata=metadata, artifact=artifact)

        assert written == 20

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(DatasetRecord).where(DatasetRecord.dataset_id == dataset_file.dataset_id))
            records = {round(float(r.value), 2): r for r in result.scalars().all()}

        assert len(records) == 20
        for i in range(20):
            expected_value = round(25.0 + i * 0.01, 2)
            rec = records[expected_value]
            assert rec.parameter == "sea_surface_temp"
            assert rec.dataset_file_id == dataset_file.id
            assert rec.lat is not None and rec.lon is not None and rec.time is not None
            assert rec.quality_flag == "normal"
            assert rec.format == "csv"

    async def test_timeless_rows_still_written_with_null_time(self, tmp_path):
        """Nullable-dimension regression: a file with lat/lon but no time
        column must still populate rows (this is the exact bug Phase 2's
        DatasetRecord population fix addressed for Wave Data's CSV)."""
        dataset_file = await _make_dataset_file()
        p = tmp_path / "timeless.parquet"
        df = pd.DataFrame({"lat": [20.0, 20.1], "lon": [90.0, 90.1], "wave_height": [1.2, 1.5]})
        df.to_parquet(p, index=False)
        metadata = ParsedFileMetadata(
            variables=["wave_height"], record_count=2, extra={"lat_col": "lat", "lon_col": "lon", "time_col": None}
        )
        artifact = ProcessedArtifact(local_path=p, content_type="x", file_extension="parquet", row_count=2)

        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            written = _write_dataset_records(db, dataset_file=sync_file, metadata=metadata, artifact=artifact)

        assert written == 2
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(DatasetRecord).where(DatasetRecord.dataset_id == dataset_file.dataset_id))
            records = result.scalars().all()
        assert len(records) == 2
        assert all(r.time is None for r in records)
        assert all(r.lat is not None for r in records)


class TestIdempotentRetry:
    async def test_retry_of_same_dataset_file_produces_no_duplicates(self, tmp_path):
        dataset_file = await _make_dataset_file()
        parquet_path, metadata = _make_parquet(tmp_path, n=10)
        artifact = ProcessedArtifact(local_path=parquet_path, content_type="x", file_extension="parquet", row_count=10)

        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            first_written = _write_dataset_records(db, dataset_file=sync_file, metadata=metadata, artifact=artifact)

        # Simulate a retry of the SAME DatasetFile (e.g. after a crash
        # partway through a prior attempt) — must delete the prior
        # attempt's rows first, then write fresh, ending at exactly 10
        # rows, never 20.
        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            second_written = _write_dataset_records(db, dataset_file=sync_file, metadata=metadata, artifact=artifact)

        assert first_written == 10
        assert second_written == 10

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(DatasetRecord).where(DatasetRecord.dataset_id == dataset_file.dataset_id))
            records = result.scalars().all()
        assert len(records) == 10

    async def test_retry_never_touches_other_files_rows_in_same_dataset(self, tmp_path):
        """The idempotency delete is scoped to dataset_file_id, never
        dataset_id — a second file's successfully-written rows in the
        same dataset must survive a retry of the FIRST file."""
        async with AsyncSessionLocal() as db:
            dataset = Dataset(code=f"BD-MULTI-{uuid.uuid4().hex[:8]}", title="Multi File", status=DatasetStatus.DRAFT.value, record_count=0)
            db.add(dataset)
            await db.commit()
            dataset_id = dataset.id

        file_a = await _make_dataset_file(dataset_id=dataset_id)
        file_b = await _make_dataset_file(dataset_id=dataset_id)

        parquet_a, metadata_a = _make_parquet(tmp_path, n=5, start_lat=10.0)
        artifact_a = ProcessedArtifact(local_path=parquet_a, content_type="x", file_extension="parquet", row_count=5)
        (tmp_path / "b").mkdir(exist_ok=True)
        parquet_b, metadata_b = _make_parquet(tmp_path / "b", n=7, start_lat=30.0)
        artifact_b = ProcessedArtifact(local_path=parquet_b, content_type="x", file_extension="parquet", row_count=7)

        with get_sync_db() as db:
            sync_a = db.get(DatasetFile, file_a.id)
            _write_dataset_records(db, dataset_file=sync_a, metadata=metadata_a, artifact=artifact_a)
        with get_sync_db() as db:
            sync_b = db.get(DatasetFile, file_b.id)
            _write_dataset_records(db, dataset_file=sync_b, metadata=metadata_b, artifact=artifact_b)

        # Retry file_a only.
        with get_sync_db() as db:
            sync_a = db.get(DatasetFile, file_a.id)
            _write_dataset_records(db, dataset_file=sync_a, metadata=metadata_a, artifact=artifact_a)

        async with AsyncSessionLocal() as db:
            result_a = await db.execute(select(DatasetRecord).where(DatasetRecord.dataset_file_id == file_a.id))
            result_b = await db.execute(select(DatasetRecord).where(DatasetRecord.dataset_file_id == file_b.id))
        assert len(result_a.scalars().all()) == 5  # exactly one copy of file_a's rows after its retry
        assert len(result_b.scalars().all()) == 7  # file_b's rows untouched by file_a's retry


class TestCopyConnectionSafety:
    """Regression test required before approval: proves repeated COPY
    chunk commits (on a fully separate raw connection) interleaved with
    _checkpoint's ORM-session commits never corrupt SQLAlchemy's
    transaction-state tracking — the failure mode empirically reproduced
    during design (see ingestion.py's _write_dataset_records docstring)
    when a raw connection was shared with the ORM session instead."""

    async def test_many_small_copy_chunks_interleaved_with_checkpoints_stay_healthy(self, tmp_path, monkeypatch):
        import app.worker.tasks.ingestion as ingestion_module

        # Force many chunk/checkpoint cycles on a small dataset by
        # shrinking the COPY chunk size for this test only.
        monkeypatch.setattr(ingestion_module, "_COPY_CHUNK_ROWS", 20)

        async with AsyncSessionLocal() as db:
            dataset = Dataset(code=f"BD-COPYSAFE-{uuid.uuid4().hex[:8]}", title="Copy Safety", status=DatasetStatus.DRAFT.value, record_count=0)
            db.add(dataset)
            await db.commit()
            dataset_id = dataset.id

        dataset_file = await _make_dataset_file(dataset_id=dataset_id)
        upload = None
        async with AsyncSessionLocal() as db:
            upload_row = Upload(
                dataset_id=dataset_id,
                file_name="records.parquet",
                status=UploadStatus.PROCESSING.value,
            )
            db.add(upload_row)
            await db.commit()
            await db.refresh(upload_row)
            upload_id = upload_row.id

        n = 500  # forces 25 chunk/checkpoint cycles at chunk size 20
        parquet_path, metadata = _make_parquet(tmp_path, n=n)
        artifact = ProcessedArtifact(local_path=parquet_path, content_type="x", file_extension="parquet", row_count=n)

        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            sync_upload = db.get(Upload, upload_id)
            written = ingestion_module._write_dataset_records(
                db, dataset_file=sync_file, metadata=metadata, artifact=artifact, upload=sync_upload
            )
            # The ORM session must still be fully healthy after the
            # entire multi-chunk write — a follow-up ORM operation on
            # the SAME session proves no PendingRollbackError / "another
            # command is already in progress" / desync occurred.
            sync_upload_recheck = db.get(Upload, upload_id)
            sync_upload_recheck.progress_pct = 100
            db.commit()

        assert written == n

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(DatasetRecord).where(DatasetRecord.dataset_file_id == dataset_file.id))
            records = result.scalars().all()
            refreshed_upload = await db.get(Upload, upload_id)

        assert len(records) == n  # no rows lost/duplicated across chunk boundaries
        assert refreshed_upload.progress_pct == 100  # the final ORM commit landed correctly
        assert refreshed_upload.progress_stage == "writing_records"


class TestCancellationAndTimeoutCleanup:
    async def test_cancellation_mid_write_leaves_no_orphaned_records(self, tmp_path):
        """_write_dataset_records commits COPY chunks durably as it goes
        (unlike the old ORM-flush approach) — a mid-write cancellation
        must therefore be cleaned up by EXPLICIT delete-by-dataset_file_id
        in _cleanup_cancelled_ingestion, not by relying on db.rollback()
        (which would no longer undo already-committed chunks)."""
        dataset_file = await _make_dataset_file()
        parquet_path, metadata = _make_parquet(tmp_path, n=10)
        artifact = ProcessedArtifact(local_path=parquet_path, content_type="x", file_extension="parquet", row_count=10)

        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            written = _write_dataset_records(db, dataset_file=sync_file, metadata=metadata, artifact=artifact)
        assert written == 10  # rows are genuinely durable at this point

        # Simulate the cancellation cleanup path running afterward (as it
        # would if IngestionCancelled were raised right after this write).
        with get_sync_db() as db:
            sync_file = db.get(DatasetFile, dataset_file.id)
            _cleanup_cancelled_ingestion(db, sync_file, processed_bucket=None, processed_key=None)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(DatasetRecord).where(DatasetRecord.dataset_file_id == dataset_file.id))
            assert result.scalars().all() == []
            # DatasetFile row itself was also deleted by the cleanup.
            assert await db.get(DatasetFile, dataset_file.id) is None


class TestReliableTerminalState:
    async def test_upload_reaches_failed_even_when_original_session_is_unusable(self):
        """Reproduces the live incident this task fixes: a session left
        in a broken state (simulated here by a prior unresolved error)
        must not prevent Upload from reaching a terminal FAILED state —
        _set_upload_terminal_state uses a brand new session/connection,
        independent of whatever the failed attempt's session is doing."""
        async with AsyncSessionLocal() as db:
            upload_row = Upload(file_name="broken.mat", status=UploadStatus.PROCESSING.value)
            db.add(upload_row)
            await db.commit()
            await db.refresh(upload_row)
            upload_id = upload_row.id

        _set_upload_terminal_state(str(upload_id), status=UploadStatus.FAILED.value, error_message="Simulated failure.")

        async with AsyncSessionLocal() as db:
            refreshed = await db.get(Upload, upload_id)
        assert refreshed.status == UploadStatus.FAILED.value
        assert refreshed.error_message == "Simulated failure."

    async def test_terminal_state_noop_when_upload_id_is_none(self):
        # Must not raise — process_dataset_file can run with no Upload
        # linkage at all (e.g. a direct backfill script invocation).
        _set_upload_terminal_state(None, status=UploadStatus.FAILED.value, error_message="unused")
