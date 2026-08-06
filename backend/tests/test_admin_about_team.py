import pytest

from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"team-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _upload_media(client, headers, *, filename="headshot.png") -> str:
    files = {"file": (filename, b"fake-png-bytes", "image/png")}
    r = await client.post("/api/v1/admin/media", files=files, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestAboutTeamCrud:
    async def test_create_list_update_delete(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS", "Manage Media"])

        r = await client.post(
            "/api/v1/admin/about-team",
            json={"name": "Dr. Farah Rahman", "role": "Lead Oceanographer", "bio": "Bio text"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        member_id = r.json()["id"]
        assert r.json()["photo_id"] is None

        r = await client.get("/api/v1/admin/about-team", headers=headers)
        assert r.status_code == 200
        assert any(m["id"] == member_id for m in r.json())

        r = await client.patch(
            f"/api/v1/admin/about-team/{member_id}", json={"role": "Senior Oceanographer"}, headers=headers
        )
        assert r.status_code == 200
        assert r.json()["role"] == "Senior Oceanographer"

        r = await client.delete(f"/api/v1/admin/about-team/{member_id}", headers=headers)
        assert r.status_code == 204

    async def test_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/about-team", json={"name": "X"}, headers=headers
        )
        assert r.status_code == 403


class TestAboutTeamPhotoReference:
    async def test_create_with_real_media_photo_id(self, client):
        headers = await _admin_headers(client, permissions=["Manage CMS", "Manage Media"])
        media_id = await _upload_media(client, headers)

        r = await client.post(
            "/api/v1/admin/about-team",
            json={"name": "Dr. Kamal Hossain", "role": "Data Scientist", "photo_id": media_id},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["photo_id"] == media_id
        assert r.json()["photo_url"] is not None

        r = await client.get("/api/v1/content/about-team")
        assert r.status_code == 200
        assert any(m["name"] == "Dr. Kamal Hossain" and m["photo_url"] is not None for m in r.json())
