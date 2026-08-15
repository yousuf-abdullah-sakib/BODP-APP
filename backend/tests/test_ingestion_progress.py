"""Real per-stage/percentage processing progress (Admin Panel production-
readiness follow-up) — proves Upload.progress_stage/progress_pct genuinely
move through real values during ingestion, not just remain permanently
null as they did before this change. Reuses the same monkeypatch-
_checkpoint technique test_multipart_upload.py's cancellation test already
established for observing internal ingestion state."""

import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.uploads import Upload, UploadStatus
from tests.test_dataset_upload import _create_dataset, _make_csv_bytes

pytestmark = pytest.mark.asyncio


class TestProcessingProgress:
    async def test_completed_upload_reports_complete_stage_and_100_pct(self, client, admin_headers):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("progress_test.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text
        upload_id = r.json()["upload"]["id"]

        r = await client.get(f"/api/v1/admin/datasets/uploads/{upload_id}", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "complete"
        assert body["progress_stage"] == "complete"
        assert body["progress_pct"] == 100

    async def test_real_ingestion_run_passes_through_real_stages(self, client, admin_headers):
        """Captures every stage/pct _checkpoint is actually called with
        during a real (eager, in-test) ingestion run — proves the values
        are genuine intermediate progress, not just a final complete=100
        stamp with nothing in between."""
        from app.worker.tasks import ingestion as ingestion_module

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("progress_stages_test.csv", _make_csv_bytes(), "text/csv")}

        seen_stages: list[tuple[str | None, int | None]] = []
        real_checkpoint = ingestion_module._checkpoint

        def _recording_checkpoint(db_, upload, *, stage=None, pct=None, **kwargs):
            if stage is not None:
                seen_stages.append((stage, pct))
            real_checkpoint(db_, upload, stage=stage, pct=pct, **kwargs)

        ingestion_module._checkpoint = _recording_checkpoint
        try:
            r = await client.post(
                f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
            )
        finally:
            ingestion_module._checkpoint = real_checkpoint
        assert r.status_code == 202, r.text

        stage_names = [s for s, _ in seen_stages]
        assert "downloading" in stage_names
        assert "parsing" in stage_names
        assert "uploading_processed" in stage_names
        assert "writing_records" in stage_names

        # downloading is always reported at 0% (the very first write, before
        # any real progress has happened yet).
        downloading_entries = [pct for s, pct in seen_stages if s == "downloading"]
        assert downloading_entries == [0]

        # At least one writing_records entry has a real, non-null percentage
        # derived from actual row progress within that stage.
        writing_pcts = [pct for s, pct in seen_stages if s == "writing_records" and pct is not None]
        assert len(writing_pcts) > 0
        assert all(0 <= p <= 99 for p in writing_pcts)

    async def test_progress_pct_never_reported_as_100_before_completion(self, client, admin_headers):
        """The writing_records stage caps its reported percentage at 99 —
        100 is reserved exclusively for the final complete stage, so a
        polling client can never see "100%" while the upload is still
        technically in progress."""
        from app.worker.tasks import ingestion as ingestion_module

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("progress_cap_test.csv", _make_csv_bytes(), "text/csv")}

        seen_pcts: list[int | None] = []
        real_checkpoint = ingestion_module._checkpoint

        def _recording_checkpoint(db_, upload, *, stage=None, pct=None, **kwargs):
            if stage is not None and stage != "complete":
                seen_pcts.append(pct)
            real_checkpoint(db_, upload, stage=stage, pct=pct, **kwargs)

        ingestion_module._checkpoint = _recording_checkpoint
        try:
            r = await client.post(
                f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
            )
        finally:
            ingestion_module._checkpoint = real_checkpoint
        assert r.status_code == 202, r.text

        assert all(p is None or p < 100 for p in seen_pcts)
