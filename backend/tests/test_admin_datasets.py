import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetCategory, DatasetFile, DatasetStatus, StorageBackend
from app.models.requests import AccessGrant, GrantStatus
from app.services.storage.keys import snapshot_key
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"ds-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _create_dataset(client, headers, title: str = "QC Test Dataset") -> str:
    r = await client.post(
        "/api/v1/admin/datasets", json={"title": title, "description": "test"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestDatasetCrud:
    async def test_create_and_list(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _create_dataset(client, headers)

        r = await client.get("/api/v1/admin/datasets", headers=headers)
        assert r.status_code == 200
        assert any(d["id"] == dataset_id for d in r.json())

    async def test_create_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Users"])
        r = await client.post(
            "/api/v1/admin/datasets", json={"title": "No Perm"}, headers=headers
        )
        assert r.status_code == 403

    async def test_get_detail_includes_active_grant_count(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _create_dataset(client, headers)

        r = await client.get(f"/api/v1/admin/datasets/{dataset_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["active_grant_count"] == 0

    async def test_update_dataset(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _create_dataset(client, headers)

        r = await client.patch(
            f"/api/v1/admin/datasets/{dataset_id}",
            json={"title": "Updated Title", "location": "Bay of Bengal"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Updated Title"
        assert r.json()["location"] == "Bay of Bengal"


class TestAutoCodeGeneration:
    """Regression coverage for a real production bug: auto-generated codes
    were derived from the total dataset COUNT rather than the highest
    existing BD-XXXX number, so deleting a dataset (or a gap from a
    custom-coded seed dataset) could make the next auto-generated code
    collide with one still in use, surfacing as a false "code already
    exists" 409 on a brand new dataset with no code specified at all."""

    async def test_deleting_a_dataset_does_not_collide_with_a_survivor(self, client):
        """The real production bug reproduced directly: with BD-0001 and
        BD-0002 both present, deleting BD-0001 drops the total dataset
        COUNT to 1 — a count-based generator computes count+1 = BD-0002,
        directly colliding with the still-alive BD-0002 and surfacing as
        a false 409 on a brand new dataset that never specified a code at
        all. The real max-based generator must skip past BD-0002 instead."""
        headers = await _admin_headers(
            client, permissions=["Edit Datasets", "Delete Datasets"]
        )
        first_id = await _create_dataset(client, headers, title="First Auto-Coded")
        second_id = await _create_dataset(client, headers, title="Second Auto-Coded")
        second_code = (
            await client.get(f"/api/v1/admin/datasets/{second_id}", headers=headers)
        ).json()["code"]
        assert second_code == "BD-0002"

        r = await client.request(
            "DELETE",
            f"/api/v1/admin/datasets/{first_id}",
            json={"confirm": True},
            headers=headers,
        )
        assert r.status_code == 204, r.text

        third_id = await _create_dataset(client, headers, title="Third Auto-Coded")
        r = await client.get(f"/api/v1/admin/datasets/{third_id}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["code"] == "BD-0003"  # not BD-0002 — no collision, no false 409

    async def test_custom_coded_dataset_does_not_collide_with_next_auto_code(self, client):
        headers = await _admin_headers(
            client, permissions=["Edit Datasets", "Delete Datasets"]
        )
        # Seed enough auto-coded datasets that the next auto-generated
        # code would be BD-0003, then create a dataset with an explicit
        # non-BD-XXXX-shaped code (mirrors real seeded datasets like
        # BD-MOD-WAVE-2024) — it must never affect subsequent auto-code
        # generation, and a further auto-coded create must still succeed.
        await _create_dataset(client, headers, title="Auto One")
        await _create_dataset(client, headers, title="Auto Two")

        r = await client.post(
            "/api/v1/admin/datasets",
            json={"title": "Custom Coded", "code": "BD-CUSTOM-2024"},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        r = await client.post(
            "/api/v1/admin/datasets", json={"title": "Auto Three"}, headers=headers
        )
        assert r.status_code == 201, r.text

    async def test_duplicate_explicit_code_still_returns_409(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        r = await client.post(
            "/api/v1/admin/datasets",
            json={"title": "Original", "code": "BD-DUPETEST"},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        r = await client.post(
            "/api/v1/admin/datasets",
            json={"title": "Duplicate", "code": "BD-DUPETEST"},
            headers=headers,
        )
        assert r.status_code == 409, r.text


class TestPublishToggle:
    async def test_publish_and_unpublish(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets", "Publish Content"])
        dataset_id = await _create_dataset(client, headers)

        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/publish", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "published"

        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/unpublish", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "draft"

    async def test_publish_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _create_dataset(client, headers)

        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/publish", headers=headers)
        assert r.status_code == 403


class TestArchive:
    async def test_archive_revokes_active_grants(self, client):
        admin_headers = await _admin_headers(
            client, permissions=["Edit Datasets", "Delete Datasets", "Approve Requests", "Publish Content"]
        )
        dataset_id = await _create_dataset(client, admin_headers)
        await client.post(f"/api/v1/admin/datasets/{dataset_id}/publish", headers=admin_headers)

        researcher_token = await register_verified_user(client, email="archive-researcher@example.com")
        researcher_headers = {"Authorization": f"Bearer {researcher_token}"}

        r = await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": "x" * 60},
            headers=researcher_headers,
        )
        assert r.status_code == 201, r.text
        request_id = r.json()["id"]

        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1m"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        grant_id = r.json()["id"]

        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/archive", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "archived"

        async with AsyncSessionLocal() as db:
            grant = await db.get(AccessGrant, uuid.UUID(grant_id))
            assert grant.status == GrantStatus.REVOKED.value

    async def test_unarchive_returns_to_draft(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets", "Delete Datasets"])
        dataset_id = await _create_dataset(client, headers)

        await client.post(f"/api/v1/admin/datasets/{dataset_id}/archive", headers=headers)
        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/unarchive", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "draft"

    async def test_archive_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _create_dataset(client, headers)
        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/archive", headers=headers)
        assert r.status_code == 403


class TestPermanentDelete:
    async def test_delete_requires_confirm(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets", "Delete Datasets"])
        dataset_id = await _create_dataset(client, headers)
        r = await client.request(
            "DELETE",
            f"/api/v1/admin/datasets/{dataset_id}",
            json={"confirm": False},
            headers=headers,
        )
        assert r.status_code == 422

    async def test_delete_cascades_and_revokes_grants(self, client):
        admin_headers = await _admin_headers(
            client, permissions=["Edit Datasets", "Delete Datasets", "Approve Requests", "Publish Content"]
        )
        dataset_id = await _create_dataset(client, admin_headers, title="Delete Me")
        await client.post(f"/api/v1/admin/datasets/{dataset_id}/publish", headers=admin_headers)

        researcher_token = await register_verified_user(client, email="delete-researcher@example.com")
        researcher_headers = {"Authorization": f"Bearer {researcher_token}"}
        r = await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": "x" * 60},
            headers=researcher_headers,
        )
        assert r.status_code == 201
        request_id = r.json()["id"]
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1m"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        grant_id = r.json()["id"]

        async with AsyncSessionLocal() as db:
            from app.models.user import User

            researcher = (
                await db.execute(select(User).where(User.email == "delete-researcher@example.com"))
            ).scalar_one()
            assert researcher.datasets_granted == 1

        r = await client.request(
            "DELETE",
            f"/api/v1/admin/datasets/{dataset_id}",
            json={"confirm": True},
            headers=admin_headers,
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            assert await db.get(Dataset, uuid.UUID(dataset_id)) is None
            # The grant row itself is gone too — AccessGrant.dataset_id
            # cascades on Dataset delete — but revoke_grant() ran first and
            # its side effects (datasets_granted decrement, audit log) must
            # have taken effect before the cascade swept the row away.
            assert await db.get(AccessGrant, uuid.UUID(grant_id)) is None
            researcher = (
                await db.execute(select(User).where(User.email == "delete-researcher@example.com"))
            ).scalar_one()
            assert researcher.datasets_granted == 0

        r = await client.get(f"/api/v1/admin/datasets/{dataset_id}", headers=admin_headers)
        assert r.status_code == 404

    async def test_delete_removes_storage_objects(self, client, monkeypatch):
        admin_headers = await _admin_headers(client, permissions=["Edit Datasets", "Delete Datasets"])
        dataset_id = await _create_dataset(client, admin_headers, title="Storage Cleanup")

        deleted_calls = []

        from app.services import admin_datasets_service

        class _FakeStorage:
            def delete(self, bucket, key):
                deleted_calls.append((bucket, key))

        monkeypatch.setattr(
            admin_datasets_service, "get_storage_backend", lambda backend: _FakeStorage()
        )

        async with AsyncSessionLocal() as db:
            dataset_file = DatasetFile(
                dataset_id=uuid.UUID(dataset_id),
                file_name="raw.csv",
                storage_backend=StorageBackend.VPS_MINIO.value,
                storage_bucket="bodp-raw",
                storage_key="raw/test/raw.csv",
                file_metadata={"processed_key": "processed/test/raw.parquet", "processed_bucket": "bodp-processed"},
            )
            db.add(dataset_file)
            await db.commit()

        r = await client.request(
            "DELETE",
            f"/api/v1/admin/datasets/{dataset_id}",
            json={"confirm": True},
            headers=admin_headers,
        )
        assert r.status_code == 204
        assert ("bodp-raw", "raw/test/raw.csv") in deleted_calls
        assert ("bodp-processed", "processed/test/raw.parquet") in deleted_calls
        # Real gap found and fixed: the Dataset Default-View Snapshot
        # (snapshots/{dataset_id}/snapshot.json, Visualize Performance
        # plan Phase 1) lives outside the per-DatasetFile loop above — a
        # live check against a real deleted dataset confirmed this key
        # was NOT being cleaned up before this fix. Deleted unconditionally
        # (idempotent even if no snapshot was ever generated for this
        # dataset), so the call must happen regardless of whether a
        # snapshot actually exists.
        assert (settings.STORAGE_VPS_BUCKET, snapshot_key(uuid.UUID(dataset_id))) in deleted_calls
