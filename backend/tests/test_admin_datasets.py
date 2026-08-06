import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetCategory, DatasetFile, DatasetStatus, StorageBackend
from app.models.requests import AccessGrant, GrantStatus
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
