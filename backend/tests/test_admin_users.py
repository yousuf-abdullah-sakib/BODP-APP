import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.audit import ActiveSession
from app.models.user import Role, User, UserRole, UserStatus
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
        return True  # mirrors a real successful send's bool return

    monkeypatch.setattr(
        email_service_module.EmailService, "send_admin_invite_email", _fake_send
    )
    return captured


async def _seed_role(name: str, permissions: list[str]) -> str:
    # _reset_database truncates `roles` before every test, so tests that
    # exercise assign/unassign need to seed their own Role row first.
    async with AsyncSessionLocal() as db:
        role = Role(name=name, description="test", permissions=permissions)
        db.add(role)
        await db.commit()
        await db.refresh(role)
        return str(role.id)


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


class TestPermanentDelete:
    async def test_delete_anonymizes_user_and_frees_email(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users"])

        target_token = await register_verified_user(client, email="to-delete@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "to-delete@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        r = await client.delete(f"/api/v1/admin/users/{user_id}", headers=admin_headers)
        assert r.status_code == 204, r.text

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.status == "deleted"
            assert user.email != "to-delete@example.com"
            assert user.full_name == "Deleted User"
            assert user.role == "user"

        # The real original email is free again — a brand-new registration
        # with the same address must succeed, not 409.
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "Reincarnated User",
                "email": "to-delete@example.com",
                "password": "testpass123",
                "institution": None,
                "phone": None,
            },
        )
        assert r.status_code == 201, r.text

    async def test_deleted_user_excluded_from_default_list_but_visible_with_filter(
        self, client, monkeypatch
    ):
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users"])

        await register_verified_user(client, email="deleted-list-user@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(
                    select(User).where(User.email == "deleted-list-user@example.com")
                )
            ).scalar_one()
            user_id = str(user.id)

        r = await client.delete(f"/api/v1/admin/users/{user_id}", headers=admin_headers)
        assert r.status_code == 204

        r = await client.get("/api/v1/admin/users", headers=admin_headers)
        assert r.status_code == 200
        assert all(u["id"] != user_id for u in r.json())

        r = await client.get(
            "/api/v1/admin/users", params={"status_filter": "deleted"}, headers=admin_headers
        )
        assert r.status_code == 200
        assert any(u["id"] == user_id for u in r.json())

    async def test_delete_removes_role_assignments_and_revokes_sessions(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users", "Manage Roles"])

        reviewer_role_id = await _seed_role("Reviewer", ["Approve Requests"])
        await register_verified_user(client, email="role-holder@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "role-holder@example.com"))
            ).scalar_one()
            user_id = user.id

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/roles/{reviewer_role_id}", headers=admin_headers
        )
        assert r.status_code == 204

        # Seed an active session directly, as if the user were logged in.
        async with AsyncSessionLocal() as db:
            session = ActiveSession(
                user_id=user_id,
                refresh_token_jti=str(uuid.uuid4()),
                device="Test Device",
                ip_address="127.0.0.1",
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            session_id = session.id

        r = await client.delete(f"/api/v1/admin/users/{user_id}", headers=admin_headers)
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            roles = (
                await db.execute(select(UserRole).where(UserRole.user_id == user_id))
            ).scalars().all()
            assert roles == []

            session = (
                await db.execute(select(ActiveSession).where(ActiveSession.id == session_id))
            ).scalar_one()
            assert session.revoked_at is not None

    async def test_cannot_delete_own_account(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users"])

        r = await client.get("/api/v1/admin/users/deletion-requests/pending", headers=admin_headers)
        assert r.status_code == 200  # sanity: acting admin isn't in this unrelated list

        # Look up the acting admin's own id via /me/profile-equivalent: reuse
        # register_verified_user's known email pattern from _admin_headers.
        async with AsyncSessionLocal() as db:
            self_user = (
                await db.execute(
                    select(User).where(User.email == "u-admin-manageusers@example.com")
                )
            ).scalar_one()
            self_id = str(self_user.id)

        r = await client.delete(f"/api/v1/admin/users/{self_id}", headers=admin_headers)
        assert r.status_code == 409

    async def test_admin_can_fulfill_a_pending_deletion_request_immediately(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users"])

        requester_token = await register_verified_user(client, email="self-requested@example.com")
        requester_headers = {"Authorization": f"Bearer {requester_token}"}
        r = await client.post("/api/v1/me/request-deletion", headers=requester_headers)
        assert r.status_code == 200, r.text
        assert r.json()["deletion_requested_at"] is not None

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "self-requested@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        # Admin fulfills the request immediately via permanent delete,
        # rather than waiting out the 30-day grace period.
        r = await client.delete(f"/api/v1/admin/users/{user_id}", headers=admin_headers)
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.status == "deleted"


class TestDeletionRequestsQueue:
    async def test_pending_deletions_list_shows_up_and_cancel_clears_it(self, client, monkeypatch):
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users"])

        requester_token = await register_verified_user(client, email="wants-deletion@example.com")
        requester_headers = {"Authorization": f"Bearer {requester_token}"}

        # Not in the queue before requesting.
        r = await client.get("/api/v1/admin/users/deletion-requests/pending", headers=admin_headers)
        assert r.status_code == 200
        assert all(u["email"] != "wants-deletion@example.com" for u in r.json())

        r = await client.post("/api/v1/me/request-deletion", headers=requester_headers)
        assert r.status_code == 200, r.text

        r = await client.get("/api/v1/admin/users/deletion-requests/pending", headers=admin_headers)
        assert r.status_code == 200
        entry = next(u for u in r.json() if u["email"] == "wants-deletion@example.com")
        assert entry["deletion_requested_at"] is not None

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "wants-deletion@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/deletion-requests/cancel", headers=admin_headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["deletion_requested_at"] is None

        r = await client.get("/api/v1/admin/users/deletion-requests/pending", headers=admin_headers)
        assert r.status_code == 200
        assert all(u["id"] != user_id for u in r.json())

        # The user's own record reflects the cancellation too, not just the
        # admin-facing queue.
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.deletion_requested_at is None

    async def test_pending_deletions_route_does_not_collide_with_user_id_path(self, client, monkeypatch):
        # GET /admin/users/deletion-requests/pending is registered before
        # GET /{user_id} specifically to avoid Starlette parsing
        # "deletion-requests" as a user_id — verify that actually holds by
        # calling it directly rather than just trusting router ordering.
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users"])
        r = await client.get("/api/v1/admin/users/deletion-requests/pending", headers=admin_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestSequentialAssignThenUnassignRegression:
    async def test_assign_administrator_then_unassign_reviewer_leaves_correct_end_state(
        self, client, monkeypatch
    ):
        # Regression test for the race-condition fix: the frontend now
        # performs "change role" as a sequential assign-then-unassign (grant
        # the new role first, then revoke the old one) rather than firing
        # both requests concurrently. admin_users_service._lock_user's row
        # lock makes this safe regardless of ordering/concurrency, but the
        # actual bug the frontend fix resolves is proven here: doing the two
        # calls in this specific sequential order must land the user in
        # exactly the correct end state (admin, holding only Administrator).
        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users", "Manage Roles"])

        reviewer_role_id = await _seed_role("Reviewer", ["Approve Requests"])
        administrator_role_id = await _seed_role("Administrator", [])

        await register_verified_user(client, email="role-switcher@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "role-switcher@example.com"))
            ).scalar_one()
            user_id = str(user.id)

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/roles/{reviewer_role_id}", headers=admin_headers
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.role == "admin"

        # Sequential: assign the new role first, then unassign the old one
        # — matching the frontend's fixed ordering.
        r = await client.post(
            f"/api/v1/admin/users/{user_id}/roles/{administrator_role_id}", headers=admin_headers
        )
        assert r.status_code == 204
        r = await client.delete(
            f"/api/v1/admin/users/{user_id}/roles/{reviewer_role_id}", headers=admin_headers
        )
        assert r.status_code == 204

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.role == "admin"

            held_role_names = (
                await db.execute(
                    select(Role.name)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == user_id)
                )
            ).scalars().all()
            assert list(held_role_names) == ["Administrator"]

    async def test_two_concurrent_assign_requests_both_land_via_row_lock(self, client, monkeypatch):
        # A lighter-weight, deterministic proof that _lock_user's
        # SELECT...FOR UPDATE actually serializes concurrent writers rather
        # than losing one under a read-then-write race: fire two distinct
        # role assignments for the same user concurrently via asyncio.gather
        # and confirm the user ends up holding BOTH roles, not just
        # whichever one committed last.
        import asyncio

        _capture_invite_token(monkeypatch)
        admin_headers = await _admin_headers(client, permissions=["Manage Users", "Manage Roles"])

        reviewer_role_id = await _seed_role("Reviewer", ["Approve Requests"])
        content_editor_role_id = await _seed_role("Content Editor", ["Manage CMS"])

        await register_verified_user(client, email="concurrent-assign@example.com")
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(
                    select(User).where(User.email == "concurrent-assign@example.com")
                )
            ).scalar_one()
            user_id = str(user.id)

        results = await asyncio.gather(
            client.post(
                f"/api/v1/admin/users/{user_id}/roles/{reviewer_role_id}", headers=admin_headers
            ),
            client.post(
                f"/api/v1/admin/users/{user_id}/roles/{content_editor_role_id}",
                headers=admin_headers,
            ),
        )
        assert all(r.status_code == 204 for r in results)

        async with AsyncSessionLocal() as db:
            held_role_names = (
                await db.execute(
                    select(Role.name)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == user_id)
                )
            ).scalars().all()
            assert set(held_role_names) == {"Reviewer", "Content Editor"}

            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.role == "admin"
