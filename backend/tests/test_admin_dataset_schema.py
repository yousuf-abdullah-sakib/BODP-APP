import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import DatasetVariable
from tests.conftest import register_verified_user
from tests.test_dataset_upload import _create_dataset, _make_csv_bytes

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"schema-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _create_dataset_with_variables(client) -> tuple[str, dict]:
    """Seeds real DatasetVariable rows through the actual Phase 2 ingestion
    pipeline (real CSV upload), rather than hand-inserting fixture rows —
    Phase 3 reviews what Phase 2 actually detects. Uses its own admin with
    "Edit Datasets" (needed to create/upload) since a caller testing
    "Review Datasets"-only permission boundaries shouldn't also need
    upload rights on their own token. Returns that admin's headers too, so
    callers that specifically want an "Edit Datasets"-only token can reuse
    it instead of registering a second user with the same email."""
    upload_headers = await _admin_headers(client, permissions=["Edit Datasets"])
    dataset_id = await _create_dataset(client, upload_headers)
    files = {"file": ("schema_review_test.csv", _make_csv_bytes(), "text/csv")}
    r = await client.post(
        f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=upload_headers
    )
    assert r.status_code == 202, r.text
    return dataset_id, upload_headers


async def _variable_id_for(dataset_id: str, name: str) -> str:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DatasetVariable).where(
                DatasetVariable.dataset_id == uuid.UUID(dataset_id), DatasetVariable.name == name
            )
        )
        return str(result.scalar_one().id)


class TestPermissionEnforcement:
    async def test_list_requires_review_datasets_permission(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        r = await client.get("/api/v1/admin/dataset-schema", headers=headers)
        assert r.status_code == 403

    async def test_list_succeeds_with_review_datasets_permission(self, client):
        headers = await _admin_headers(client, permissions=["Review Datasets"])
        r = await client.get("/api/v1/admin/dataset-schema", headers=headers)
        assert r.status_code == 200

    async def test_edit_datasets_alone_cannot_update_roles(self, client):
        # _create_dataset_with_variables already registers and uploads
        # through its own internal "Edit Datasets"-only admin — reuse that
        # same token here instead of registering a second "Edit
        # Datasets"-only user (which would collide on the unique email
        # constraint within a single test).
        dataset_id, edit_only_headers = await _create_dataset_with_variables(client)
        variable_id = await _variable_id_for(dataset_id, "sea_surface_temp")

        r = await client.patch(
            f"/api/v1/admin/dataset-schema/{dataset_id}/variables/{variable_id}",
            json={"roles": ["data_variable"]},
            headers=edit_only_headers,
        )
        assert r.status_code == 403


class TestReviewWorkflow:
    async def test_new_dataset_appears_unreviewed(self, client):
        headers = await _admin_headers(client, permissions=["Review Datasets"])
        dataset_id, _upload_headers = await _create_dataset_with_variables(client)

        r = await client.get("/api/v1/admin/dataset-schema?unreviewed_only=true", headers=headers)
        assert r.status_code == 200
        ids = {row["dataset_id"] for row in r.json()}
        assert dataset_id in ids

        row = next(row for row in r.json() if row["dataset_id"] == dataset_id)
        assert row["schema_reviewed_at"] is None
        assert row["unassigned_count"] == row["variable_count"] > 0

    async def test_detail_lists_detected_variables(self, client):
        headers = await _admin_headers(client, permissions=["Review Datasets"])
        dataset_id, _upload_headers = await _create_dataset_with_variables(client)

        r = await client.get(f"/api/v1/admin/dataset-schema/{dataset_id}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        names = {v["name"] for v in body["variables"]}
        assert {"time", "lat", "lon", "sea_surface_temp"}.issubset(names)
        assert all(v["roles"] == [] for v in body["variables"])

    async def test_assign_role_and_mark_reviewed(self, client):
        headers = await _admin_headers(client, permissions=["Review Datasets"])
        dataset_id, _upload_headers = await _create_dataset_with_variables(client)

        r = await client.get(f"/api/v1/admin/dataset-schema/{dataset_id}", headers=headers)
        variables = r.json()["variables"]

        for v in variables:
            role = "dimension" if v["is_dimension"] else "data_variable"
            r = await client.patch(
                f"/api/v1/admin/dataset-schema/{dataset_id}/variables/{v['id']}",
                json={"roles": [role]},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json()["roles"] == [role]

        r = await client.post(
            f"/api/v1/admin/dataset-schema/{dataset_id}/mark-reviewed", headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["schema_reviewed_at"] is not None

        r = await client.get(f"/api/v1/admin/dataset-schema/{dataset_id}", headers=headers)
        assert r.json()["schema_reviewed_at"] is not None
        assert r.json()["schema_reviewed_by_name"] == "Fixture User"

    async def test_mark_reviewed_rejects_when_variables_unassigned(self, client):
        headers = await _admin_headers(client, permissions=["Review Datasets"])
        dataset_id, _upload_headers = await _create_dataset_with_variables(client)

        r = await client.post(
            f"/api/v1/admin/dataset-schema/{dataset_id}/mark-reviewed", headers=headers
        )
        assert r.status_code == 409

    async def test_changing_roles_after_review_clears_review_state(self, client):
        headers = await _admin_headers(client, permissions=["Review Datasets"])
        dataset_id, _upload_headers = await _create_dataset_with_variables(client)

        r = await client.get(f"/api/v1/admin/dataset-schema/{dataset_id}", headers=headers)
        for v in r.json()["variables"]:
            role = "dimension" if v["is_dimension"] else "data_variable"
            await client.patch(
                f"/api/v1/admin/dataset-schema/{dataset_id}/variables/{v['id']}",
                json={"roles": [role]},
                headers=headers,
            )
        r = await client.post(
            f"/api/v1/admin/dataset-schema/{dataset_id}/mark-reviewed", headers=headers
        )
        assert r.status_code == 200

        sst_id = await _variable_id_for(dataset_id, "sea_surface_temp")
        r = await client.patch(
            f"/api/v1/admin/dataset-schema/{dataset_id}/variables/{sst_id}",
            json={"roles": ["data_variable", "visualization_variable"]},
            headers=headers,
        )
        assert r.status_code == 200

        r = await client.get(f"/api/v1/admin/dataset-schema/{dataset_id}", headers=headers)
        assert r.json()["schema_reviewed_at"] is None


class TestMultiRoleAssignment:
    async def test_variable_can_hold_multiple_roles_simultaneously(self, client):
        headers = await _admin_headers(client, permissions=["Review Datasets"])
        dataset_id, _upload_headers = await _create_dataset_with_variables(client)
        sst_id = await _variable_id_for(dataset_id, "sea_surface_temp")

        r = await client.patch(
            f"/api/v1/admin/dataset-schema/{dataset_id}/variables/{sst_id}",
            json={"roles": ["data_variable", "visualization_variable"]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert set(r.json()["roles"]) == {"data_variable", "visualization_variable"}

    async def test_unknown_role_rejected(self, client):
        headers = await _admin_headers(client, permissions=["Review Datasets"])
        dataset_id, _upload_headers = await _create_dataset_with_variables(client)
        sst_id = await _variable_id_for(dataset_id, "sea_surface_temp")

        r = await client.patch(
            f"/api/v1/admin/dataset-schema/{dataset_id}/variables/{sst_id}",
            json={"roles": ["not_a_real_role"]},
            headers=headers,
        )
        assert r.status_code == 422


class TestDatasetCrudRegression:
    async def test_existing_dataset_crud_unaffected(self, client):
        """Regression check (PLAN.md Phase 3 requirement): the new schema-
        review columns/router must not disturb existing dataset CRUD."""
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        dataset_id = await _create_dataset(client, headers)

        r = await client.get(f"/api/v1/admin/datasets/{dataset_id}", headers=headers)
        assert r.status_code == 200, r.text

        r = await client.patch(
            f"/api/v1/admin/datasets/{dataset_id}",
            json={"title": "Renamed via regression test"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "Renamed via regression test"
