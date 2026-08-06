import pytest

from app.core.database import AsyncSessionLocal
from app.models.admin import CmsBlock
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"cms-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_system_block(*, page: str = "home", key: str = "hero-title") -> str:
    async with AsyncSessionLocal() as db:
        block = CmsBlock(
            key=key, page=page, section="hero", label="Hero Title",
            value="Original System Value", display_order=0, is_system_block=True, is_active=True,
        )
        db.add(block)
        await db.commit()
        await db.refresh(block)
        return str(block.id)


class TestCmsBlockCrud:
    async def test_create_list_update(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS"])

        r = await client.post(
            "/api/v1/admin/cms/blocks",
            json={"key": "custom-banner", "page": "home", "value": "<p>Banner</p>"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        block_id = r.json()["id"]
        assert r.json()["is_system_block"] is False

        r = await client.get("/api/v1/admin/cms/blocks", params={"page": "home"}, headers=headers)
        assert r.status_code == 200
        assert any(b["id"] == block_id for b in r.json())

        r = await client.patch(
            f"/api/v1/admin/cms/blocks/{block_id}", json={"value": "<p>Updated Banner</p>"}, headers=headers
        )
        assert r.status_code == 200
        assert r.json()["value"] == "<p>Updated Banner</p>"

    async def test_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/cms/blocks", json={"key": "x", "page": "home"}, headers=headers
        )
        assert r.status_code == 403


class TestCmsCustomBlockDelete:
    async def test_custom_block_create_and_delete_succeeds(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS"])
        r = await client.post(
            "/api/v1/admin/cms/blocks",
            json={"key": "custom-deletable", "page": "about"},
            headers=headers,
        )
        block_id = r.json()["id"]

        r = await client.delete(f"/api/v1/admin/cms/blocks/{block_id}", headers=headers)
        assert r.status_code == 204


class TestCmsSystemBlockDeleteBlocked:
    async def test_system_block_delete_returns_403(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS"])
        block_id = await _seed_system_block()

        r = await client.delete(f"/api/v1/admin/cms/blocks/{block_id}", headers=headers)
        assert r.status_code == 403


class TestCmsSanitization:
    async def test_script_tag_stripped_on_value(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS"])
        r = await client.post(
            "/api/v1/admin/cms/blocks",
            json={"key": "xss-block", "page": "home", "value": "<p>Safe</p><script>alert(1)</script>"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert "<script>" not in r.json()["value"]
        assert "Safe" in r.json()["value"]


class TestCmsCacheInvalidation:
    async def test_patch_value_immediately_visible_on_public_endpoint(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS"])
        block_id = await _seed_system_block(page="home", key="hero-title")

        # Warm the public cache with the original value first.
        r = await client.get("/api/v1/content/cms-blocks/home")
        assert r.status_code == 200
        assert any(b["value"] == "Original System Value" for b in r.json())

        r = await client.patch(
            f"/api/v1/admin/cms/blocks/{block_id}", json={"value": "Freshly Edited Value"}, headers=headers
        )
        assert r.status_code == 200

        r = await client.get("/api/v1/content/cms-blocks/home")
        assert r.status_code == 200
        values = [b["value"] for b in r.json()]
        assert "Freshly Edited Value" in values
        assert "Original System Value" not in values
