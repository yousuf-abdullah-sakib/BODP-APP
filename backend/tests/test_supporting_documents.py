import io
import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetCategory, DatasetStatus
from app.models.requests import DatasetRequest, RequestSupportingDocument
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio

_LONG_JUSTIFICATION = (
    "I am researching coastal water quality trends and need this dataset "
    "to calibrate my model against real observational records."
)

_MINIMAL_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


async def _seed_published_dataset(code: str) -> str:
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name=f"Cat-{code}", description="test", color_tag="cat-Environmental")
        db.add(category)
        await db.flush()
        dataset = Dataset(
            code=code,
            title="Supporting Doc Test Dataset",
            description="test",
            category_id=category.id,
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.commit()
        await db.refresh(dataset)
        return str(dataset.id)


async def _admin_headers(client, email: str = "supdoc-admin@example.com") -> dict:
    token = await register_verified_user(client, email=email, admin=True, permissions=["Approve Requests"])
    return {"Authorization": f"Bearer {token}"}


async def _researcher_headers(client, email: str = "supdoc-researcher@example.com") -> dict:
    token = await register_verified_user(client, email=email)
    return {"Authorization": f"Bearer {token}"}


def _submit_form(dataset_id: str, **overrides) -> dict:
    data = {"dataset_id": dataset_id, "justification": _LONG_JUSTIFICATION}
    data.update(overrides)
    return data


class TestSubmitWithoutDocument:
    async def test_request_without_document_succeeds_normally(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-NONE")
        headers = await _researcher_headers(client, email="supdoc-none@example.com")
        r = await client.post("/api/v1/requests", data=_submit_form(dataset_id), headers=headers)
        assert r.status_code == 201, r.text
        assert r.json()["supporting_document"] is None


class TestSubmitWithValidDocument:
    async def test_valid_pdf_under_3mb_succeeds_and_links(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-OK")
        headers = await _researcher_headers(client, email="supdoc-ok@example.com")
        files = {"file": ("justification.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")}
        r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["supporting_document"] is not None
        assert body["supporting_document"]["original_filename"] == "justification.pdf"
        assert body["supporting_document"]["content_type"] == "application/pdf"
        assert body["supporting_document"]["file_size_bytes"] == len(_MINIMAL_PDF)

        async with AsyncSessionLocal() as db:
            req = (
                await db.execute(select(DatasetRequest).where(DatasetRequest.id == uuid.UUID(body["id"])))
            ).scalar_one()
            assert req.supporting_document_id is not None
            doc = (
                await db.execute(
                    select(RequestSupportingDocument).where(
                        RequestSupportingDocument.id == req.supporting_document_id
                    )
                )
            ).scalar_one()
            assert doc.request_id == req.id
            assert doc.storage_key.startswith(f"supporting-documents/{req.id}/")


class TestOversizedDocument:
    async def test_document_over_3mb_rejected_no_partial_storage(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-BIG")
        headers = await _researcher_headers(client, email="supdoc-big@example.com")
        oversized = _MINIMAL_PDF + b"0" * (3 * 1024 * 1024 + 1)
        files = {"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")}
        r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert r.status_code == 400, r.text

        # No request left behind, no orphaned document row.
        async with AsyncSessionLocal() as db:
            requests_for_user = (
                await db.execute(select(DatasetRequest))
            ).scalars().all()
            assert len(requests_for_user) == 0
            docs = (await db.execute(select(RequestSupportingDocument))).scalars().all()
            assert len(docs) == 0

    async def test_exactly_3mb_boundary_accepted(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-EXACT")
        headers = await _researcher_headers(client, email="supdoc-exact@example.com")
        exactly_3mb = _MINIMAL_PDF + b"0" * (3 * 1024 * 1024 - len(_MINIMAL_PDF))
        assert len(exactly_3mb) == 3 * 1024 * 1024
        files = {"file": ("exact.pdf", io.BytesIO(exactly_3mb), "application/pdf")}
        r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert r.status_code == 201, r.text
        assert r.json()["supporting_document"]["file_size_bytes"] == 3 * 1024 * 1024

    async def test_1kb_over_3mb_boundary_rejected(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-OVER")
        headers = await _researcher_headers(client, email="supdoc-over@example.com")
        just_over = _MINIMAL_PDF + b"0" * (3 * 1024 * 1024 - len(_MINIMAL_PDF) + 1024)
        files = {"file": ("over.pdf", io.BytesIO(just_over), "application/pdf")}
        r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert r.status_code == 400, r.text
        body = r.json()
        assert "exceeds" in body["error"]["message"], f"body={body!r}"


class TestInvalidFileType:
    async def test_unsupported_extension_rejected(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-EXT")
        headers = await _researcher_headers(client, email="supdoc-ext@example.com")
        files = {"file": ("script.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")}
        r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert r.status_code == 400, r.text

        async with AsyncSessionLocal() as db:
            requests_for_user = (await db.execute(select(DatasetRequest))).scalars().all()
            assert len(requests_for_user) == 0

    async def test_content_mismatch_rejected(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-MISMATCH")
        headers = await _researcher_headers(client, email="supdoc-mismatch@example.com")
        # .pdf extension but not actually PDF content (magic bytes wrong).
        files = {"file": ("fake.pdf", io.BytesIO(b"not a real pdf file at all"), "application/pdf")}
        r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert r.status_code == 400, r.text


class TestStorageFailureCleanup:
    async def test_storage_upload_failure_does_not_leave_request_behind(self, client, monkeypatch):
        from app.services import supporting_document_service

        class _FailingStorage:
            def ensure_bucket(self, bucket):
                pass

            def put(self, *a, **k):
                raise RuntimeError("simulated storage outage")

            def delete(self, bucket, key):
                pass

        monkeypatch.setattr(
            supporting_document_service, "get_storage_backend", lambda backend: _FailingStorage()
        )

        dataset_id = await _seed_published_dataset("BD-SUPDOC-STOFAIL")
        headers = await _researcher_headers(client, email="supdoc-stofail@example.com")
        files = {"file": ("doc.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")}
        r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert r.status_code == 500, r.text

        async with AsyncSessionLocal() as db:
            requests_for_user = (await db.execute(select(DatasetRequest))).scalars().all()
            assert len(requests_for_user) == 0, "request must not survive a storage failure"
            docs = (await db.execute(select(RequestSupportingDocument))).scalars().all()
            assert len(docs) == 0

    async def test_db_failure_after_storage_cleans_up_uploaded_object(self, client, monkeypatch):
        from app.services import supporting_document_service

        deleted_keys = []

        class _RecordingStorage:
            def ensure_bucket(self, bucket):
                pass

            def put(self, bucket, key, data, *, content_type=None):
                from app.services.storage.base import StorageObject

                return StorageObject(bucket=bucket, key=key, size_bytes=len(_MINIMAL_PDF), etag="fake")

            def delete(self, bucket, key):
                deleted_keys.append(key)

        monkeypatch.setattr(
            supporting_document_service, "get_storage_backend", lambda backend: _RecordingStorage()
        )

        from sqlalchemy.ext.asyncio import AsyncSession

        original_flush = AsyncSession.flush

        async def _selectively_failing_flush(self, *a, **k):
            # Only fail the flush that would persist the new
            # RequestSupportingDocument row — the request-creation flush
            # (requests_service.create_request) must still succeed so this
            # test exercises "DB failure AFTER storage succeeded", not a
            # failure before the request even exists.
            if any(isinstance(obj, RequestSupportingDocument) for obj in self.new):
                raise RuntimeError("simulated DB failure")
            return await original_flush(self, *a, **k)

        monkeypatch.setattr(AsyncSession, "flush", _selectively_failing_flush)

        dataset_id = await _seed_published_dataset("BD-SUPDOC-DBFAIL")
        headers = await _researcher_headers(client, email="supdoc-dbfail@example.com")
        files = {"file": ("doc.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")}
        r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert r.status_code in (500, 422), r.text
        assert len(deleted_keys) >= 1, "uploaded storage object must be cleaned up on DB failure"


class TestAdminDownload:
    async def test_admin_can_download_existing_document(self, client, monkeypatch):
        from app.services import supporting_document_service
        import app.routers.admin_requests as admin_requests_router

        class _FakeStorage:
            def ensure_bucket(self, bucket):
                pass

            def put(self, bucket, key, data, *, content_type=None):
                from app.services.storage.base import StorageObject

                return StorageObject(bucket=bucket, key=key, size_bytes=len(_MINIMAL_PDF), etag="fake")

            def presign_get(self, bucket, key, *, expires_in_seconds=3600):
                return f"https://fake-storage.local/{bucket}/{key}?sig=abc"

        fake_storage = _FakeStorage()
        monkeypatch.setattr(supporting_document_service, "get_storage_backend", lambda backend: fake_storage)
        monkeypatch.setattr(admin_requests_router, "get_storage_backend", lambda backend: fake_storage)

        dataset_id = await _seed_published_dataset("BD-SUPDOC-DL")
        headers = await _researcher_headers(client, email="supdoc-dl@example.com")
        files = {"file": ("doc.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")}
        submit_r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        assert submit_r.status_code == 201, submit_r.text
        request_id = submit_r.json()["id"]

        admin_headers = await _admin_headers(client)
        r = await client.get(
            f"/api/v1/admin/requests/{request_id}/supporting-document/download", headers=admin_headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["filename"] == "doc.pdf"
        assert "download_url" in r.json()

    async def test_non_admin_cannot_access_download_endpoint(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-UNAUTH")
        headers = await _researcher_headers(client, email="supdoc-unauth@example.com")
        files = {"file": ("doc.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")}
        submit_r = await client.post(
            "/api/v1/requests", data=_submit_form(dataset_id), files=files, headers=headers
        )
        request_id = submit_r.json()["id"]

        # The requester itself (not an admin with Approve Requests) must not
        # be able to hit the admin download endpoint.
        r = await client.get(
            f"/api/v1/admin/requests/{request_id}/supporting-document/download", headers=headers
        )
        assert r.status_code == 403

    async def test_missing_document_handled_gracefully(self, client):
        dataset_id = await _seed_published_dataset("BD-SUPDOC-MISSING")
        headers = await _researcher_headers(client, email="supdoc-missing@example.com")
        r = await client.post("/api/v1/requests", data=_submit_form(dataset_id), headers=headers)
        request_id = r.json()["id"]

        admin_headers = await _admin_headers(client)
        dl_r = await client.get(
            f"/api/v1/admin/requests/{request_id}/supporting-document/download", headers=admin_headers
        )
        assert dl_r.status_code == 404

    async def test_download_for_nonexistent_request_404s(self, client):
        admin_headers = await _admin_headers(client)
        r = await client.get(
            f"/api/v1/admin/requests/{uuid.uuid4()}/supporting-document/download", headers=admin_headers
        )
        assert r.status_code == 404
