import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.user import PERMISSION_LIST, Role, User
from app.services import email_service as email_service_module
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"team-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_administrator_role() -> None:
    # _reset_database truncates `roles` before every test, so the real
    # migration-seeded "Administrator" row never exists in this suite —
    # invite_admin looks it up by exact name, so tests that exercise the
    # full-access-on-invite behavior need to seed it themselves first.
    async with AsyncSessionLocal() as db:
        db.add(Role(name="Administrator", description="test", permissions=list(PERMISSION_LIST)))
        await db.commit()


def _capture_invite_token(monkeypatch) -> list[str]:
    captured = []

    def _fake_send(self, to, full_name, token, *, is_admin):
        captured.append(token)

    monkeypatch.setattr(email_service_module.EmailService, "send_admin_invite_email", _fake_send)
    return captured


class TestAdminTeamInvite:
    async def test_invite_creates_admin_user_and_roster_entry(self, client, monkeypatch):
        await _seed_administrator_role()
        captured = _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={"name": "New Admin", "email": "new-admin@example.com", "role_label": "Data Manager"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        member = r.json()
        assert member["user_id"] is not None

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "new-admin@example.com"))
            ).scalar_one()
            assert user.role == "admin"

        token = captured[0]
        r = await client.post(
            "/api/v1/auth/confirm-invite", json={"token": token, "new_password": "adminpass123"}
        )
        assert r.status_code == 204

        r = await client.post(
            "/api/v1/auth/login", json={"email": "new-admin@example.com", "password": "adminpass123"}
        )
        assert r.status_code == 200
        new_admin_token = r.json()["tokens"]["access_token"]

        # Confirm real admin-gated access (require_admin coarse gate).
        r = await client.get(
            "/api/v1/admin/overview", headers={"Authorization": f"Bearer {new_admin_token}"}
        )
        assert r.status_code == 200

        # Confirm the invited admin also got real fine-grained permissions
        # (require_permission gate) — this is the actual bug: an invited
        # admin previously had role='admin' but no attached Role, so every
        # permission-gated section (the vast majority of the admin panel)
        # 403'd for them despite being able to log in and reach Overview.
        r = await client.get(
            "/api/v1/admin/audit-log",
            headers={"Authorization": f"Bearer {new_admin_token}"},
        )
        assert r.status_code == 200, r.text

    async def test_invite_requires_permission(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        r = await client.post(
            "/api/v1/admin/team",
            json={"name": "X", "email": "x-admin@example.com"},
            headers=headers,
        )
        assert r.status_code == 403


class TestAdminTeamRemoval:
    async def test_remove_suspends_underlying_user(self, client, monkeypatch):
        captured = _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={"name": "Removable Admin", "email": "removable-admin@example.com"},
            headers=headers,
        )
        member_id = r.json()["id"]

        token = captured[0]
        await client.post(
            "/api/v1/auth/confirm-invite", json={"token": token, "new_password": "adminpass123"}
        )

        r = await client.delete(f"/api/v1/admin/team/{member_id}", headers=headers)
        assert r.status_code == 204

        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "removable-admin@example.com", "password": "adminpass123"},
        )
        assert r.status_code == 403
