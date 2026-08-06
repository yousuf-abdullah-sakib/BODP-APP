import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.admin import MediaFile
from app.services.storage.registry import default_bucket_for, get_storage_backend
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"media-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, headers, *, filename="photo.png", content=b"fake-png-bytes", mime="image/png"):
    files = {"file": (filename, content, mime)}
    return await client.post("/api/v1/admin/media", files=files, headers=headers)


class TestMediaUpload:
    async def test_upload_persists_real_row_and_storage_object(self, client):
        headers = await _admin_headers(client, permissions=["Manage Media"])
        r = await _upload(client, headers, filename="team_photo.png")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["file_name"] == "team_photo.png"
        assert body["mime_type"] == "image/png"
        assert body["url"].startswith("http")

        async with AsyncSessionLocal() as db:
            media = await db.get(MediaFile, uuid.UUID(body["id"]))
            assert media is not None
            assert media.size_bytes == len(b"fake-png-bytes")

            storage = get_storage_backend(media.storage_backend)
            bucket = default_bucket_for(media.storage_backend)
            assert storage.exists(bucket, media.storage_key) is True

    async def test_upload_rejects_unsupported_extension(self, client):
        headers = await _admin_headers(client, permissions=["Manage Media"])
        r = await _upload(client, headers, filename="malware.exe", mime="application/octet-stream")
        assert r.status_code == 400

    async def test_upload_rejects_empty_file(self, client):
        headers = await _admin_headers(client, permissions=["Manage Media"])
        r = await _upload(client, headers, content=b"")
        assert r.status_code == 400

    async def test_upload_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS"])
        r = await _upload(client, headers)
        assert r.status_code == 403


class TestMediaListSearchFilter:
    async def test_list_search_and_mime_filter(self, client):
        headers = await _admin_headers(client, permissions=["Manage Media"])
        await _upload(client, headers, filename="sunset_beach.png", mime="image/png")
        await _upload(
            client, headers, filename="report.csv", content=b"a,b,c\n1,2,3", mime="text/csv"
        )

        r = await client.get("/api/v1/admin/media", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 2

        r = await client.get("/api/v1/admin/media", params={"search": "sunset"}, headers=headers)
        assert r.status_code == 200
        names = [m["file_name"] for m in r.json()]
        assert names == ["sunset_beach.png"]

        r = await client.get("/api/v1/admin/media", params={"mime_prefix": "image/"}, headers=headers)
        assert r.status_code == 200
        names = {m["file_name"] for m in r.json()}
        assert names == {"sunset_beach.png"}


class TestMediaDeleteReferenceGuard:
    async def test_delete_blocked_while_referenced_by_blog_post_then_succeeds(self, client):
        headers = await _admin_headers(
            client, permissions=["Manage Media", "Manage Blog"]
        )
        r = await _upload(client, headers, filename="featured.png")
        media_id = r.json()["id"]

        r = await client.post(
            "/api/v1/admin/blog",
            json={"title": "Has Featured Image", "featured_image_id": media_id},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        post_id = r.json()["id"]

        r = await client.delete(f"/api/v1/admin/media/{media_id}", headers=headers)
        assert r.status_code == 409

        r = await client.patch(
            f"/api/v1/admin/blog/{post_id}", json={"featured_image_id": None}, headers=headers
        )
        assert r.status_code == 200
        assert r.json()["featured_image_id"] is None

        r = await client.delete(f"/api/v1/admin/media/{media_id}", headers=headers)
        assert r.status_code == 204

    async def test_delete_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Media"])
        r = await _upload(client, headers)
        media_id = r.json()["id"]

        other_headers = await _admin_headers(client, permissions=["Manage CMS"])
        r = await client.delete(f"/api/v1/admin/media/{media_id}", headers=other_headers)
        assert r.status_code == 403
