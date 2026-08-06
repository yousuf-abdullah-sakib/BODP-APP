import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.user import User, UserStatus
from app.services import email_service as email_service_module
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"u-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


def _capture_invite_token(monkeypatch) -> list[str]:
    captured = []

    def _fake_send(self, to, full_name, token, *, is_admin):
        captured.append(token)

    monkeypatch.setattr(
        email_service_module.EmailService, "send_admin_invite_email", _fake_send
    )
    return captured


class TestUserCrud:
    async def test_create_lists_only_researcher_role(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/users",
            json={"full_name": "New Researcher", "email": "new-researcher@example.com"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "user"
        user_id = r.json()["id"]

        r = await client.get("/api/v1/admin/users", headers=headers)
        assert r.status_code == 200
        assert any(u["id"] == user_id for u in r.json())

    async def test_create_requires_permission(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        r = await client.post(
            "/api/v1/admin/users",
            json={"full_name": "X", "email": "x@example.com"},
            headers=headers,
        )
        assert r.status_code == 403

    async def test_update_user(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])
        r = await client.post(
            "/api/v1/admin/users",
            json={"full_name": "Original Name", "email": "update-me@example.com"},
            headers=headers,
        )
        user_id = r.json()["id"]

        r = await client.patch(
            f"/api/v1/admin/users/{user_id}",
            json={"institution": "BODP Institute"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["institution"] == "BODP Institute"


class TestInviteFlow:
    async def test_invite_confirm_and_login(self, client, monkeypatch):
        captured = _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/users",
            json={"full_name": "Invited User", "email": "invited@example.com"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert len(captured) == 1
        token = captured[0]

        r = await client.post(
            "/api/v1/auth/confirm-invite",
            json={"token": token, "new_password": "newpass123"},
        )
        assert r.status_code == 204, r.text

        r = await client.post(
            "/api/v1/auth/login", json={"email": "invited@example.com", "password": "newpass123"}
        )
        assert r.status_code == 200, r.text


class TestSuspension:
    async def test_suspend_blocks_login(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users"])

        researcher_token = await register_verified_user(client, email="suspend-me@example.com")

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "suspend-me@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        r = await client.post(f"/api/v1/admin/users/{user_id}/suspend", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "suspended"

        r = await client.post(
            "/api/v1/auth/login", json={"email": "suspend-me@example.com", "password": "testpass123"}
        )
        assert r.status_code == 403

        r = await client.post(f"/api/v1/admin/users/{user_id}/activate", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "active"

        r = await client.post(
            "/api/v1/auth/login", json={"email": "suspend-me@example.com", "password": "testpass123"}
        )
        assert r.status_code == 200


class TestGrantRevokeFromDetailView:
    async def test_revoke_grant_from_user_detail(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(
            client, permissions=["Manage Users", "Edit Datasets", "Publish Content", "Approve Requests"]
        )

        r = await client.post(
            "/api/v1/admin/datasets", json={"title": "Grant Test Dataset"}, headers=admin_headers
        )
        dataset_id = r.json()["id"]
        await client.post(f"/api/v1/admin/datasets/{dataset_id}/publish", headers=admin_headers)

        researcher_token = await register_verified_user(client, email="grantee@example.com")
        researcher_headers = {"Authorization": f"Bearer {researcher_token}"}
        r = await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": "x" * 60},
            headers=researcher_headers,
        )
        request_id = r.json()["id"]
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1m"},
            headers=admin_headers,
        )
        grant_id = r.json()["id"]

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "grantee@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        r = await client.get(f"/api/v1/admin/users/{user_id}/grants", headers=admin_headers)
        assert r.status_code == 200
        assert any(g["id"] == grant_id for g in r.json())

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/grants/{grant_id}/revoke", headers=admin_headers
        )
        assert r.status_code == 200
        assert r.json()["status"] == "revoked"


class TestCsvExport:
    async def test_export_contains_real_rows(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])
        await client.post(
            "/api/v1/admin/users",
            json={"full_name": "CSV Export User", "email": "csv-user@example.com"},
            headers=headers,
        )

        r = await client.get("/api/v1/admin/users/export.csv", headers=headers)
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "CSV Export User" in r.text
        assert "csv-user@example.com" in r.text
