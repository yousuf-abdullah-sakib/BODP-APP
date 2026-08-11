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


async def _seed_role(name: str, permissions: list[str]) -> str:
    # _reset_database truncates `roles` before every test, so the real
    # migration-seeded roles never exist in this suite — tests that
    # exercise invite/assign need to seed their own Role row first.
    async with AsyncSessionLocal() as db:
        role = Role(name=name, description="test", permissions=permissions)
        db.add(role)
        await db.commit()
        await db.refresh(role)
        return str(role.id)


def _capture_invite_token(monkeypatch) -> list[str]:
    captured = []

    def _fake_send(self, to, full_name, token, *, is_admin):
        captured.append(token)
        return True  # mirrors a real successful send's bool return

    monkeypatch.setattr(email_service_module.EmailService, "send_admin_invite_email", _fake_send)
    return captured


class TestAdminTeamInvite:
    async def test_invite_creates_admin_user_with_real_role(self, client, monkeypatch):
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        captured = _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={"full_name": "New Admin", "email": "new-admin@example.com", "role_id": administrator_role_id},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        member = r.json()
        assert member["roles"] == ["Administrator"]

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
        # (require_permission gate) via the real Role, not a free-text label.
        r = await client.get(
            "/api/v1/admin/audit-log",
            headers={"Authorization": f"Bearer {new_admin_token}"},
        )
        assert r.status_code == 200, r.text

    async def test_invite_with_non_administrator_role_still_grants_coarse_admin_access(
        self, client, monkeypatch
    ):
        # The core of this feature: ANY of the 4 admin-panel roles must
        # grant coarse role='admin', not just Administrator — otherwise a
        # "Data Manager" invite would be stuck 403ing everywhere despite
        # holding the right fine-grained permission.
        data_manager_role_id = await _seed_role("Data Manager", ["Edit Datasets"])
        captured = _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={
                "full_name": "New Data Manager",
                "email": "new-data-manager@example.com",
                "role_id": data_manager_role_id,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["roles"] == ["Data Manager"]

        token = captured[0]
        await client.post(
            "/api/v1/auth/confirm-invite", json={"token": token, "new_password": "adminpass123"}
        )
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "new-data-manager@example.com", "password": "adminpass123"},
        )
        assert r.status_code == 200
        new_admin_token = r.json()["tokens"]["access_token"]

        target_headers = {"Authorization": f"Bearer {new_admin_token}"}
        # Coarse gate passes (role='admin' was set despite not being
        # "Administrator").
        r = await client.get("/api/v1/admin/overview", headers=target_headers)
        assert r.status_code == 200
        # Their actual permission (Edit Datasets) works.
        r = await client.post(
            "/api/v1/admin/datasets", json={"title": "X"}, headers=target_headers
        )
        assert r.status_code == 201, r.text
        # A permission they DON'T hold is still rejected.
        r = await client.get("/api/v1/admin/audit-log", headers=target_headers)
        assert r.status_code == 403

    async def test_invite_requires_permission(self, client, monkeypatch):
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Edit Datasets"])
        r = await client.post(
            "/api/v1/admin/team",
            json={"full_name": "X", "email": "x-admin@example.com", "role_id": administrator_role_id},
            headers=headers,
        )
        assert r.status_code == 403

    async def test_invite_surfaces_email_sent_true_on_success(self, client, monkeypatch):
        # No monkeypatch on send() itself here — dev-mode printing (no SMTP
        # configured in the test env) returns True, so this exercises the
        # real success path, not a forced patch.
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={
                "full_name": "Emailed Admin",
                "email": "emailed-admin@example.com",
                "role_id": administrator_role_id,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["email_sent"] is True

    async def test_invite_surfaces_email_sent_false_on_failure(self, client, monkeypatch):
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        headers = await _admin_headers(client, permissions=["Manage Users"])

        # Patch the low-level send(), not the send_admin_invite_email
        # wrapper — send() is what actually decides success/failure; the
        # wrapper just forwards its bool return.
        monkeypatch.setattr(
            email_service_module.EmailService, "send", lambda self, *a, **k: False
        )

        r = await client.post(
            "/api/v1/admin/team",
            json={
                "full_name": "Unemailed Admin",
                "email": "unemailed-admin@example.com",
                "role_id": administrator_role_id,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["email_sent"] is False

        # The account itself must still be created despite the send
        # failure — email delivery is best-effort, not a precondition.
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(
                    select(User).where(User.email == "unemailed-admin@example.com")
                )
            ).scalar_one()
            assert user.role == "admin"


class TestAdminTeamList:
    async def test_list_includes_every_admin_regardless_of_how_they_became_admin(self, client):
        # Regression test for the exact bug reported: Admin Management only
        # showed admins created via "Invite Admin" (the old AdminTeamMember
        # roster row). An admin who got role='admin' + a Role attached via
        # admin_users_service.assign_role (Users section / Roles &
        # Permissions) must ALSO show up — the list is derived from real
        # User+Role data now, not a separate roster table.
        reviewer_role_id = await _seed_role("Reviewer", ["Approve Requests"])
        headers = await _admin_headers(client, permissions=["Manage Users"])

        # A plain researcher, promoted via the Users-section assign-role
        # endpoint — NOT via "Invite Admin".
        target_token = await register_verified_user(client, email="promoted-admin@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "promoted-admin@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/roles/{reviewer_role_id}", headers=headers
        )
        assert r.status_code == 204

        r = await client.get("/api/v1/admin/team", headers=headers)
        assert r.status_code == 200
        emails = [m["email"] for m in r.json()]
        assert "promoted-admin@example.com" in emails
        entry = next(m for m in r.json() if m["email"] == "promoted-admin@example.com")
        assert entry["roles"] == ["Reviewer"]


class TestAdminTeamRemoval:
    async def test_delete_permanently_anonymizes_underlying_user(self, client, monkeypatch):
        # DELETE /admin/team/{id} is now permanent (routes through
        # admin_users_service.anonymize_user), NOT suspend-only — suspend is
        # its own separate, reversible endpoint (see TestSuspendReactivate).
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        captured = _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={
                "full_name": "Removable Admin",
                "email": "removable-admin@example.com",
                "role_id": administrator_role_id,
            },
            headers=headers,
        )
        user_id = r.json()["id"]

        token = captured[0]
        await client.post(
            "/api/v1/auth/confirm-invite", json={"token": token, "new_password": "adminpass123"}
        )

        r = await client.delete(f"/api/v1/admin/team/{user_id}", headers=headers)
        assert r.status_code == 204

        # The original email no longer belongs to any account (it was
        # rewritten to the deleted-user placeholder), so login now fails as
        # "no such account" (401), not "account suspended" (403) — that
        # distinction is itself proof this is a real anonymization, not a
        # suspend under a different name.
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "removable-admin@example.com", "password": "adminpass123"},
        )
        assert r.status_code == 401

        # Permanent, not just suspended: the row was anonymized, so the
        # original email is free again and no longer resolves to the old
        # identity.
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.email != "removable-admin@example.com"
            assert user.status == "deleted"
            assert user.full_name == "Deleted User"

        r = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "New Owner",
                "email": "removable-admin@example.com",
                "password": "testpass123",
                "institution": None,
                "phone": None,
            },
        )
        assert r.status_code == 201, r.text

    async def test_cannot_delete_own_account(self, client):
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        headers = await _admin_headers(client, permissions=["Manage Users"])

        # Find the acting admin's own user_id via the team list.
        r = await client.get("/api/v1/admin/team", headers=headers)
        assert r.status_code == 200
        self_id = next(
            m["id"] for m in r.json() if m["email"] == "team-admin-manageusers@example.com"
        )

        r = await client.delete(f"/api/v1/admin/team/{self_id}", headers=headers)
        assert r.status_code == 409


class TestSuspendReactivate:
    async def test_suspend_then_reactivate_is_reversible(self, client, monkeypatch):
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        captured = _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={
                "full_name": "Suspendable Admin",
                "email": "suspendable-admin@example.com",
                "role_id": administrator_role_id,
            },
            headers=headers,
        )
        user_id = r.json()["id"]
        token = captured[0]
        await client.post(
            "/api/v1/auth/confirm-invite", json={"token": token, "new_password": "adminpass123"}
        )

        r = await client.post(f"/api/v1/admin/team/{user_id}/suspend", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "suspended"
        # Roles must survive a suspend — it's reversible, unlike delete.
        assert r.json()["roles"] == ["Administrator"]

        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "suspendable-admin@example.com", "password": "adminpass123"},
        )
        assert r.status_code == 403

        r = await client.post(f"/api/v1/admin/team/{user_id}/reactivate", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"
        assert r.json()["roles"] == ["Administrator"]

        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "suspendable-admin@example.com", "password": "adminpass123"},
        )
        assert r.status_code == 200


class TestResendInvite:
    async def test_resend_invite_sends_a_fresh_token(self, client, monkeypatch):
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        captured = _capture_invite_token(monkeypatch)
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={
                "full_name": "Resend Admin",
                "email": "resend-admin@example.com",
                "role_id": administrator_role_id,
            },
            headers=headers,
        )
        user_id = r.json()["id"]
        original_token = captured[0]

        r = await client.post(f"/api/v1/admin/team/{user_id}/resend-invite", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"email_sent": True}

        # A fresh token was sent (captured a second time) and it's usable.
        assert len(captured) == 2
        new_token = captured[1]
        assert new_token != original_token

        r = await client.post(
            "/api/v1/auth/confirm-invite", json={"token": new_token, "new_password": "adminpass123"}
        )
        assert r.status_code == 204, r.text

    async def test_resend_invite_surfaces_send_failure(self, client, monkeypatch):
        # Deliberately does NOT use _capture_invite_token here — that helper
        # patches send_admin_invite_email itself (the wrapper), which would
        # shadow the low-level send() patch below and make the resend call
        # still appear to succeed. Patching send() directly is what actually
        # exercises the real failure path end-to-end through the wrapper.
        administrator_role_id = await _seed_role("Administrator", list(PERMISSION_LIST))
        headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.post(
            "/api/v1/admin/team",
            json={
                "full_name": "Resend Fail Admin",
                "email": "resend-fail-admin@example.com",
                "role_id": administrator_role_id,
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        user_id = r.json()["id"]

        monkeypatch.setattr(
            email_service_module.EmailService, "send", lambda self, *a, **k: False
        )
        r = await client.post(f"/api/v1/admin/team/{user_id}/resend-invite", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"email_sent": False}
