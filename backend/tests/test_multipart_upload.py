import urllib.request
import uuid

import pytest

from app.core.database import AsyncSessionLocal, get_sync_db
from app.models.catalog import DatasetFile
from app.models.uploads import Upload, UploadStatus
from tests.conftest import register_verified_user
from tests.test_dataset_upload import _make_csv_bytes

pytestmark = pytest.mark.asyncio


async def _admin_headers(client) -> dict:
    token = await register_verified_user(
        client, email="multipart-admin@example.com", admin=True, permissions=["Edit Datasets"]
    )
    return {"Authorization": f"Bearer {token}"}


async def _create_dataset(client, headers) -> str:
    r = await client.post(
        "/api/v1/admin/datasets",
        json={"title": "Multipart Test Dataset", "description": "for multipart upload tests"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _put_part(url: str, data: bytes) -> str:
    req = urllib.request.Request(url, data=data, method="PUT")
    with urllib.request.urlopen(req) as resp:
        return resp.headers.get("ETag").strip('"')


async def _run_full_multipart_upload(client, headers, dataset_id, *, filename="large_test.csv"):
    """Drives the real HTTP endpoints end-to-end, exactly as the frontend
    does: initiate -> for each part, presign + PUT direct to MinIO + mark
    complete -> complete. Uses a 2-part CSV comfortably over the 5MiB
    S3 multipart minimum-part-size floor so completion is exercised for
    real, not just a degenerate single-part case.

    The declared total_size_bytes at initiate time is set just over
    PART_SIZE_BYTES (64MiB) purely to force total_parts=2 from the
    server's ceil(total_size_bytes / PART_SIZE_BYTES) math — the actual
    bytes PUT per part only need to satisfy S3's real constraint (every
    part but the last >= 5MiB), not match that declared total exactly;
    complete_multipart_upload only verifies part COUNT against storage's
    own list_parts(), never aggregate size. Generating a real 64MiB+
    payload here would make this test needlessly slow for no benefit.
    """
    from app.services.dataset_multipart_upload_service import PART_SIZE_BYTES

    part_size = 5 * 1024 * 1024 + 1024
    header = b"time,lat,lon,value\n"
    row = b"2024-01-01,22.0,91.0,27.5\n"
    part1 = header + row * ((part_size - len(header)) // len(row))
    part2 = row * 100

    r = await client.post(
        f"/api/v1/admin/datasets/{dataset_id}/uploads/initiate",
        json={"filename": filename, "total_size_bytes": PART_SIZE_BYTES + 1},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    upload_id = body["upload"]["id"]
    assert body["upload"]["status"] == "initiated"
    total_parts = body["total_parts"]
    assert total_parts == 2

    for part_number, data in enumerate([part1, part2], start=1):
        r = await client.post(
            f"/api/v1/admin/datasets/uploads/{upload_id}/parts/{part_number}/presign", headers=headers
        )
        assert r.status_code == 200, r.text
        upload_url = r.json()["upload_url"]

        etag = _put_part(upload_url, data)
        assert etag

        r = await client.post(
            f"/api/v1/admin/datasets/uploads/{upload_id}/parts/complete",
            json={"part_number": part_number, "size_bytes": len(data)},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["uploaded_bytes"] > 0

    r = await client.post(f"/api/v1/admin/datasets/uploads/{upload_id}/complete", headers=headers)
    assert r.status_code == 200, r.text
    return r.json(), upload_id


class TestMultipartUploadHappyPath:
    async def test_initiate_creates_session(self, client):
        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)

        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/uploads/initiate",
            json={"filename": "big.nc", "total_size_bytes": 200 * 1024 * 1024},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["upload"]["status"] == "initiated"
        assert body["total_parts"] >= 1
        assert body["part_size_bytes"] > 0

    async def test_full_upload_completes_and_dispatches_ingestion(self, client):
        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)

        result, upload_id = await _run_full_multipart_upload(client, headers, dataset_id)
        assert result["upload"]["status"] in ("queued", "complete")  # eager Celery may finish immediately
        assert result["dataset_file"]["file_format"] == "csv"

        # Ingestion runs eagerly in tests (see conftest) — poll once, same
        # convention as the small-file upload tests.
        r = await client.get(f"/api/v1/admin/datasets/uploads/{upload_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "complete"

    async def test_complete_fails_if_parts_missing(self, client):
        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)

        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/uploads/initiate",
            json={"filename": "incomplete.csv", "total_size_bytes": 10 * 1024 * 1024},
            headers=headers,
        )
        upload_id = r.json()["upload"]["id"]

        # Never PUT any parts — complete must reject, not silently finalize
        # an empty/partial object.
        r = await client.post(f"/api/v1/admin/datasets/uploads/{upload_id}/complete", headers=headers)
        assert r.status_code == 400
        assert "incomplete" in r.json()["error"]["message"].lower()

    async def test_presign_part_out_of_range_rejected(self, client):
        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)

        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/uploads/initiate",
            json={"filename": "small.csv", "total_size_bytes": 1024},
            headers=headers,
        )
        upload_id = r.json()["upload"]["id"]
        total_parts = r.json()["total_parts"]

        r = await client.post(
            f"/api/v1/admin/datasets/uploads/{upload_id}/parts/{total_parts + 1}/presign", headers=headers
        )
        assert r.status_code == 400

    async def test_initiate_enforces_admin_configured_size_limit(self, client):
        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)

        r = await client.patch(
            "/api/v1/admin/settings/general", json={"max_upload_size_mb": 1}, headers=headers
        )
        assert r.status_code == 200, r.text

        try:
            r = await client.post(
                f"/api/v1/admin/datasets/{dataset_id}/uploads/initiate",
                json={"filename": "toobig.nc", "total_size_bytes": 5 * 1024 * 1024},
                headers=headers,
            )
            assert r.status_code == 400
        finally:
            await client.patch(
                "/api/v1/admin/settings/general", json={"max_upload_size_mb": 5000}, headers=headers
            )


class TestMultipartUploadCancellation:
    """Covers the two cancellation scenarios specified in the Phase 1 plan:
    (a) uploading -> cancel -> cleanup; (b) upload completes -> processing
    starts -> cancel -> processing stops safely, partial artifacts cleaned up."""

    async def test_cancel_during_initiated_aborts_multipart_session(self, client):
        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)

        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/uploads/initiate",
            json={"filename": "cancel_me.nc", "total_size_bytes": 50 * 1024 * 1024},
            headers=headers,
        )
        upload_id = r.json()["upload"]["id"]

        r = await client.post(f"/api/v1/admin/datasets/uploads/{upload_id}/cancel", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"

        # A cancelled upload must never remain/transition to processing —
        # completing it after cancellation must be rejected.
        r = await client.post(f"/api/v1/admin/datasets/uploads/{upload_id}/complete", headers=headers)
        assert r.status_code == 400

        async with AsyncSessionLocal() as db:
            upload = await db.get(Upload, uuid.UUID(upload_id))
            assert upload.status == UploadStatus.CANCELLED.value
            # Never became an orphaned dataset/file — no DatasetFile row
            # was ever created for a cancelled-before-complete upload.
            assert upload.dataset_file_id is None

    async def test_cancel_after_complete_before_processing_deletes_file(self, client):
        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)

        # Complete a real upload via the service layer directly (bypassing
        # the router's automatic ingestion dispatch) so we can observe the
        # QUEUED-but-not-yet-PROCESSING window explicitly. 5MiB+100 bytes is
        # well under PART_SIZE_BYTES (64MiB), so this is deterministically
        # a single-part upload — no need to loop over multiple parts.
        from app.services import dataset_multipart_upload_service as svc

        file_bytes = b"x" * (5 * 1024 * 1024 + 100)

        async with AsyncSessionLocal() as db:
            upload, total_parts = await svc.initiate_multipart_upload(
                db,
                dataset_id=uuid.UUID(dataset_id),
                filename="queued_cancel.csv",
                total_size_bytes=len(file_bytes),
                uploaded_by=None,
            )
            upload_id = upload.id
        assert total_parts == 1

        async with AsyncSessionLocal() as db:
            url = await svc.presign_part(db, upload_id=upload_id, part_number=1)
        _put_part(url, file_bytes)
        async with AsyncSessionLocal() as db:
            await svc.mark_part_uploaded(db, upload_id=upload_id, part_number=1, size_bytes=len(file_bytes))
            upload, dataset_file = await svc.complete_multipart_upload(db, upload_id=upload_id)
            assert upload.status == UploadStatus.QUEUED.value
            dataset_file_id = dataset_file.id
            storage_bucket, storage_key = dataset_file.storage_bucket, dataset_file.storage_key

        # Now cancel while still QUEUED (ingestion never dispatched in this
        # direct-service-call path) — must delete the DatasetFile row and
        # its raw object, never leave it orphaned.
        r = await client.post(f"/api/v1/admin/datasets/uploads/{upload_id}/cancel", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"

        async with AsyncSessionLocal() as db:
            assert await db.get(DatasetFile, dataset_file_id) is None
            upload_row = await db.get(Upload, upload_id)
            assert upload_row.dataset_file_id is None

        from app.services.storage.registry import get_storage_backend

        storage = get_storage_backend("vps_minio")
        assert storage.exists(storage_bucket, storage_key) is False

    async def test_cancel_already_terminal_upload_rejected(self, client):
        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)

        result, upload_id = await _run_full_multipart_upload(client, headers, dataset_id, filename="done.csv")

        r = await client.get(f"/api/v1/admin/datasets/uploads/{upload_id}", headers=headers)
        assert r.json()["status"] == "complete"

        r = await client.post(f"/api/v1/admin/datasets/uploads/{upload_id}/cancel", headers=headers)
        assert r.status_code == 400


class TestIngestionCancellationCheckpoints:
    """Exercises process_dataset_file's actual cancellation-checkpoint
    logic (ingestion.py) directly — the second required scenario: upload
    completes -> processing starts -> cancel -> processing stops safely
    and partial artifacts are cleaned up.

    Celery runs eagerly in the test suite (see conftest), so there's no
    real concurrent worker to race a cancel request against mid-task;
    instead this drives _run_ingestion directly against a DatasetFile/
    Upload pair created via the real small-file upload path, and flips
    Upload.status to CANCELLED between checkpoints — exactly what
    dataset_multipart_upload_service.cancel_upload does for a real
    in-flight PROCESSING upload, just triggered deterministically instead
    of via a timing race."""

    async def _upload_raw_file_without_ingesting(self, client, headers, dataset_id) -> tuple[str, str]:
        """Uses the real upload endpoint's underlying service directly
        (bypassing the router, which auto-dispatches ingestion) so the raw
        file lands in storage and DatasetFile/Upload rows exist, but
        nothing has been ingested yet — mirroring the exact state
        process_dataset_file starts from."""
        from app.services.dataset_file_service import upload_dataset_file

        class _FakeUploadFile:
            def __init__(self, data: bytes):
                self._data = data
                self._sent = False

            async def read(self, size: int) -> bytes:
                if self._sent:
                    return b""
                self._sent = True
                return self._data

        async with AsyncSessionLocal() as db:
            upload, dataset_file = await upload_dataset_file(
                db,
                dataset_id=uuid.UUID(dataset_id),
                filename="cancel_during_processing.csv",
                file_stream=_FakeUploadFile(_make_csv_bytes()),
                uploaded_by=None,
            )
            return str(upload.id), str(dataset_file.id)

    async def test_cancellation_between_checkpoints_stops_and_cleans_up(self, client):
        from app.worker.tasks import ingestion as ingestion_module

        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)
        upload_id, dataset_file_id = await self._upload_raw_file_without_ingesting(
            client, headers, dataset_id
        )

        # Simulate a cancel request arriving while the task is between
        # checkpoint 1 (after raw download) and checkpoint 2 (after
        # conversion) — flip Upload.status to CANCELLED directly on the
        # SAME session object the running task's own checkpoints will see
        # (process_dataset_file opens its own get_sync_db() session
        # internally, separate from any session a test might open outside
        # it — mutating a different session's copy of the row and
        # committing that OTHER session would never be visible here).
        call_count = {"n": 0}
        real_checkpoint = ingestion_module._checkpoint

        def _checkpoint_cancel_on_second_call(db_, upload, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2 and upload is not None:
                # Simulate the cancel request landing right now — mutate
                # and commit via db_/upload, the task's own session and
                # its own Upload instance.
                upload.status = UploadStatus.CANCELLED.value
                db_.commit()
            real_checkpoint(db_, upload, **kwargs)

        ingestion_module._checkpoint = _checkpoint_cancel_on_second_call
        try:
            result = ingestion_module.process_dataset_file.run(dataset_file_id, upload_id)
        finally:
            ingestion_module._checkpoint = real_checkpoint

        assert result["status"] == "cancelled"

        async with AsyncSessionLocal() as db:
            upload = await db.get(Upload, uuid.UUID(upload_id))
            assert upload.status == UploadStatus.CANCELLED.value

            # The DatasetFile row (created before ingestion started, when
            # the raw upload completed) must be gone — a cancelled upload
            # must not remain as an orphaned dataset/file.
            assert await db.get(DatasetFile, uuid.UUID(dataset_file_id)) is None

    async def test_cancellation_before_task_starts_never_begins_ingestion(self, client):
        """If Upload.status is already CANCELLED by the time the Celery
        task actually starts running (cancelled while merely queued,
        before the worker picked it up), the task must recognize this
        immediately and never begin ingestion at all."""
        from app.worker.tasks import ingestion as ingestion_module

        headers = await _admin_headers(client)
        dataset_id = await _create_dataset(client, headers)
        upload_id, dataset_file_id = await self._upload_raw_file_without_ingesting(
            client, headers, dataset_id
        )

        with get_sync_db() as db:
            upload_row = db.get(Upload, uuid.UUID(upload_id))
            upload_row.status = UploadStatus.CANCELLED.value
            db.commit()

        result = ingestion_module.process_dataset_file.run(dataset_file_id, upload_id)
        assert result["status"] == "cancelled"

        async with AsyncSessionLocal() as db:
            # DatasetFile is left untouched in this path — the task
            # returned before doing anything, so there's nothing to clean
            # up beyond what the /cancel endpoint itself already handled
            # for a QUEUED upload.
            assert await db.get(DatasetFile, uuid.UUID(dataset_file_id)) is not None
