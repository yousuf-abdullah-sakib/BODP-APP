import pytest

from app.core.database import AsyncSessionLocal
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"cat-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


class TestCategoryCrud:
    async def test_create_list_update_delete(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])

        r = await client.post(
            "/api/v1/admin/categories",
            json={"name": "Hydrological", "description": "test", "color_tag": "cat-Hydrological"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        category_id = r.json()["id"]
        assert r.json()["dataset_count"] == 0

        r = await client.get("/api/v1/admin/categories", headers=headers)
        assert r.status_code == 200
        assert any(c["id"] == category_id for c in r.json())

        r = await client.patch(
            f"/api/v1/admin/categories/{category_id}",
            json={"description": "updated"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["description"] == "updated"

        r = await client.delete(f"/api/v1/admin/categories/{category_id}", headers=headers)
        assert r.status_code == 204

    async def test_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Users"])
        r = await client.post(
            "/api/v1/admin/categories", json={"name": "X"}, headers=headers
        )
        assert r.status_code == 403

    async def test_duplicate_name_rejected(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        r = await client.post("/api/v1/admin/categories", json={"name": "Atmospheric"}, headers=headers)
        assert r.status_code == 201
        r = await client.post("/api/v1/admin/categories", json={"name": "Atmospheric"}, headers=headers)
        assert r.status_code == 409


class TestCategoryDeleteNullifiesDatasets:
    async def test_delete_nullifies_attached_datasets(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])

        r = await client.post(
            "/api/v1/admin/categories", json={"name": "Model Data"}, headers=headers
        )
        category_id = r.json()["id"]

        r = await client.post(
            "/api/v1/admin/datasets",
            json={"title": "Categorized Dataset", "category_id": category_id},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        dataset_id = r.json()["id"]
        assert r.json()["category_id"] == category_id

        r = await client.delete(f"/api/v1/admin/categories/{category_id}", headers=headers)
        assert r.status_code == 204

        r = await client.get(f"/api/v1/admin/datasets/{dataset_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["category_id"] is None
        assert r.json()["category_name"] is None
