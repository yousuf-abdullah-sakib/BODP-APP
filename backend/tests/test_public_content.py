import pytest

from app.core.database import AsyncSessionLocal
from app.models.admin import AboutTeamMember, CmsBlock
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"public-content-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


class TestPublicCmsBlocks:
    async def test_only_active_blocks_returned_no_auth(self, client):
        async with AsyncSessionLocal() as db:
            db.add_all(
                [
                    CmsBlock(key="active-block", page="home", value="Visible", is_active=True),
                    CmsBlock(key="inactive-block", page="home", value="Hidden", is_active=False),
                ]
            )
            await db.commit()

        # No Authorization header at all — this is a public, unauthenticated read.
        r = await client.get("/api/v1/content/cms-blocks/home")
        assert r.status_code == 200, r.text
        keys = {b["key"] for b in r.json()}
        assert "active-block" in keys
        assert "inactive-block" not in keys

    async def test_scoped_to_requested_page(self, client):
        async with AsyncSessionLocal() as db:
            db.add_all(
                [
                    CmsBlock(key="home-only", page="home", value="Home", is_active=True),
                    CmsBlock(key="about-only", page="about", value="About", is_active=True),
                ]
            )
            await db.commit()

        r = await client.get("/api/v1/content/cms-blocks/about")
        assert r.status_code == 200
        keys = {b["key"] for b in r.json()}
        assert keys == {"about-only"}


class TestPublicBlogList:
    async def test_only_published_posts_returned_no_auth(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/blog", json={"title": "Public Published", "status": "published"}, headers=headers
        )
        published_id = r.json()["id"]
        r = await client.post(
            "/api/v1/admin/blog", json={"title": "Public Draft", "status": "draft"}, headers=headers
        )
        draft_id = r.json()["id"]

        r = await client.get("/api/v1/content/blog")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()}
        assert published_id in ids
        assert draft_id not in ids

    async def test_unpublished_detail_404s_publicly(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/blog", json={"title": "Still Draft", "status": "draft"}, headers=headers
        )
        draft_id = r.json()["id"]

        r = await client.get(f"/api/v1/content/blog/{draft_id}")
        assert r.status_code == 404


class TestPublicTeamList:
    async def test_returns_team_members_no_auth(self, client):
        async with AsyncSessionLocal() as db:
            db.add(AboutTeamMember(name="Public Team Member", role="Researcher", display_order=0))
            await db.commit()

        r = await client.get("/api/v1/content/about-team")
        assert r.status_code == 200, r.text
        names = {m["name"] for m in r.json()}
        assert "Public Team Member" in names
