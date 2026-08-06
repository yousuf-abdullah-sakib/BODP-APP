import pytest

from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"blog-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


class TestBlogCrud:
    async def test_create_list_get_update_delete(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])

        r = await client.post(
            "/api/v1/admin/blog",
            json={"title": "Monsoon Outlook 2026", "category": "Research", "content_html": "<p>Body</p>"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        post_id = r.json()["id"]
        assert r.json()["status"] == "draft"

        r = await client.get("/api/v1/admin/blog", headers=headers)
        assert r.status_code == 200
        assert any(p["id"] == post_id for p in r.json())

        r = await client.get(f"/api/v1/admin/blog/{post_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["content_html"] == "<p>Body</p>"

        r = await client.patch(
            f"/api/v1/admin/blog/{post_id}", json={"title": "Updated Title"}, headers=headers
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Updated Title"

        r = await client.delete(f"/api/v1/admin/blog/{post_id}", headers=headers)
        assert r.status_code == 204

    async def test_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS"])
        r = await client.post("/api/v1/admin/blog", json={"title": "X"}, headers=headers)
        assert r.status_code == 403


class TestBlogPublishUnpublish:
    async def test_publish_and_unpublish(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/blog", json={"title": "Draft Post"}, headers=headers
        )
        post_id = r.json()["id"]
        assert r.json()["status"] == "draft"

        r = await client.post(f"/api/v1/admin/blog/{post_id}/publish", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "published"

        r = await client.post(f"/api/v1/admin/blog/{post_id}/unpublish", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "draft"


class TestBlogSanitization:
    async def test_script_tag_stripped_on_create(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/blog",
            json={"title": "XSS Attempt", "content_html": "<p>Safe</p><script>alert(1)</script>"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert "<script>" not in r.json()["content_html"]
        assert "Safe" in r.json()["content_html"]

    async def test_script_tag_stripped_on_update(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/blog", json={"title": "To Update", "content_html": "<p>Original</p>"}, headers=headers
        )
        post_id = r.json()["id"]

        r = await client.patch(
            f"/api/v1/admin/blog/{post_id}",
            json={"content_html": "<img src=x onerror=alert(1)><p>New</p>"},
            headers=headers,
        )
        assert r.status_code == 200
        assert "onerror" not in r.json()["content_html"]
        assert "New" in r.json()["content_html"]


class TestPublicBlogVisibility:
    async def test_public_list_only_returns_published(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])

        r = await client.post(
            "/api/v1/admin/blog", json={"title": "Published Post", "status": "published"}, headers=headers
        )
        published_id = r.json()["id"]
        r = await client.post(
            "/api/v1/admin/blog", json={"title": "Draft Post", "status": "draft"}, headers=headers
        )
        draft_id = r.json()["id"]

        r = await client.get("/api/v1/content/blog")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()}
        assert published_id in ids
        assert draft_id not in ids

    async def test_public_detail_increments_views(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/blog", json={"title": "View Count Post", "status": "published"}, headers=headers
        )
        post_id = r.json()["id"]
        assert r.json()["views"] == 0

        r = await client.get(f"/api/v1/content/blog/{post_id}")
        assert r.status_code == 200
        assert r.json()["views"] == 1

        r = await client.get(f"/api/v1/content/blog/{post_id}")
        assert r.status_code == 200
        assert r.json()["views"] == 2
