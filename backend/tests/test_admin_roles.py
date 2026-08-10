import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.user import PERMISSION_LIST, User
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"role-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


class TestRoleCrud:
    async def test_create_list_update_delete(self, client):
        headers = await _admin_headers(client, permissions=["Manage Roles"])

        r = await client.post(
            "/api/v1/admin/roles",
            json={"name": "Data Reviewer", "description": "test", "permissions": ["View Analytics"]},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        role_id = r.json()["id"]
        assert r.json()["user_count"] == 0

        r = await client.get("/api/v1/admin/roles", headers=headers)
        assert r.status_code == 200
        assert any(role["id"] == role_id for role in r.json())

        r = await client.patch(
            f"/api/v1/admin/roles/{role_id}",
            json={"permissions": ["View Analytics", "Manage Backups"]},
            headers=headers,
        )
        assert r.status_code == 200
        assert set(r.json()["permissions"]) == {"View Analytics", "Manage Backups"}

        r = await client.delete(f"/api/v1/admin/roles/{role_id}", headers=headers)
        assert r.status_code == 204

    async def test_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        r = await client.post(
            "/api/v1/admin/roles", json={"name": "X", "permissions": []}, headers=headers
        )
        assert r.status_code == 403

    async def test_unknown_permission_rejected(self, client):
        headers = await _admin_headers(client, permissions=["Manage Roles"])
        r = await client.post(
            "/api/v1/admin/roles",
            json={"name": "Bad Role", "permissions": ["Not A Real Permission"]},
            headers=headers,
        )
        assert r.status_code == 422


class TestRoleDeletionEffects:
    async def test_delete_role_removes_assignment_and_flips_permission_check(self, client):
        admin_headers = await _admin_headers(client, permissions=["Manage Roles", "Manage Users"])

        r = await client.post(
            "/api/v1/admin/roles",
            json={"name": "Temp Analyst", "permissions": ["Manage Roles"]},
            headers=admin_headers,
        )
        role_id = r.json()["id"]

        target_token = await register_verified_user(client, email="temp-analyst@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "temp-analyst@example.com"))
            ).scalar_one()
            user.role = "admin"
            await db.commit()
            user_id = str(user.id)

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/roles/{role_id}", headers=admin_headers
        )
        assert r.status_code == 204

        target_headers = {"Authorization": f"Bearer {target_token}"}
        # The role grants "Manage Roles" — this endpoint requires exactly
        # that permission, so it's a real test of the assignment, not just
        # the coarse admin gate.
        r = await client.get("/api/v1/admin/roles", headers=target_headers)
        assert r.status_code == 200

        r = await client.get("/api/v1/admin/roles", headers=admin_headers)
        assert r.status_code == 200
        assert next(role["user_count"] for role in r.json() if role["id"] == role_id) == 1

        r = await client.delete(f"/api/v1/admin/roles/{role_id}", headers=admin_headers)
        assert r.status_code == 204

        # Permission enforcement verified per-role via a real API call, not
        # just UI: after the role is deleted, the same user is now blocked.
        r = await client.get("/api/v1/admin/roles", headers=target_headers)
        assert r.status_code == 403

    async def test_cannot_delete_system_roles(self, client):
        headers = await _admin_headers(client, permissions=["Manage Roles"])
        r = await client.post(
            "/api/v1/admin/roles", json={"name": "Administrator", "permissions": []}, headers=headers
        )
        role_id = r.json()["id"]
        r = await client.delete(f"/api/v1/admin/roles/{role_id}", headers=headers)
        assert r.status_code == 409

    async def test_cannot_rename_system_roles(self, client):
        headers = await _admin_headers(client, permissions=["Manage Roles"])
        r = await client.post(
            "/api/v1/admin/roles", json={"name": "Administrator", "permissions": []}, headers=headers
        )
        role_id = r.json()["id"]
        r = await client.patch(
            f"/api/v1/admin/roles/{role_id}", json={"name": "Not Administrator Anymore"}, headers=headers
        )
        assert r.status_code == 409

        # Renaming to its own current name is a no-op, not a conflict.
        r = await client.patch(
            f"/api/v1/admin/roles/{role_id}",
            json={"name": "Administrator", "description": "still full access"},
            headers=headers,
        )
        assert r.status_code == 200


class TestAdministratorRoleSyncsCoarseAccess:
    """The 'Administrator' fine-grained Role is the single source of truth
    for coarse admin access — require_permission() checks BOTH `role ==
    'admin'` AND the fine-grained permission, so the two must never drift
    apart. See admin_users_service.assign_role/unassign_role."""

    async def test_assigning_administrator_role_grants_coarse_admin_access(self, client):
        admin_headers = await _admin_headers(client, permissions=["Manage Roles", "Manage Users"])

        r = await client.post(
            "/api/v1/admin/roles",
            json={"name": "Administrator", "permissions": list(PERMISSION_LIST)},
            headers=admin_headers,
        )
        assert r.status_code == 201, r.text
        role_id = r.json()["id"]

        # A plain researcher account — role='user', not 'admin' — assigned
        # the Administrator role via Roles & Permissions (not Admin
        # Management/invite).
        target_token = await register_verified_user(client, email="promoted-researcher@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(
                    select(User).where(User.email == "promoted-researcher@example.com")
                )
            ).scalar_one()
            assert user.role == "user"
            user_id = str(user.id)

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/roles/{role_id}", headers=admin_headers
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.role == "admin"

        # Real proof, not just the DB column: a coarse-admin-gated endpoint
        # (require_admin, no fine-grained permission needed) now works too.
        target_headers = {"Authorization": f"Bearer {target_token}"}
        r = await client.get("/api/v1/admin/overview", headers=target_headers)
        assert r.status_code == 200, r.text

    async def test_unassigning_administrator_role_revokes_coarse_admin_access(self, client):
        admin_headers = await _admin_headers(client, permissions=["Manage Roles", "Manage Users"])

        r = await client.post(
            "/api/v1/admin/roles",
            json={"name": "Administrator", "permissions": list(PERMISSION_LIST)},
            headers=admin_headers,
        )
        role_id = r.json()["id"]

        target_token = await register_verified_user(client, email="demoted-admin@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "demoted-admin@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/roles/{role_id}", headers=admin_headers
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.role == "admin"

        r = await client.delete(
            f"/api/v1/admin/users/{user_id}/roles/{role_id}", headers=admin_headers
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.role == "user"

        target_headers = {"Authorization": f"Bearer {target_token}"}
        r = await client.get("/api/v1/admin/overview", headers=target_headers)
        assert r.status_code == 403

    async def test_other_roles_never_grant_coarse_admin_access(self, client):
        # A non-Administrator Role — even one granting many permissions —
        # must never flip the coarse role column. Only the Role literally
        # named "Administrator" does.
        admin_headers = await _admin_headers(client, permissions=["Manage Roles", "Manage Users"])

        r = await client.post(
            "/api/v1/admin/roles",
            json={"name": "Content Editor", "permissions": ["Manage CMS", "Manage Blog"]},
            headers=admin_headers,
        )
        role_id = r.json()["id"]

        target_token = await register_verified_user(client, email="content-editor@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "content-editor@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/roles/{role_id}", headers=admin_headers
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.role == "user"

        # No coarse admin access — require_permission's first check (role
        # == 'admin') rejects before even looking at the fine-grained
        # permission the role actually grants.
        target_headers = {"Authorization": f"Bearer {target_token}"}
        r = await client.get("/api/v1/admin/cms/blocks?page=home", headers=target_headers)
        assert r.status_code == 403
