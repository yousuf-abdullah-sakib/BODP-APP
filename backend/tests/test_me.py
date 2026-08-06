import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.admin import CmsBlock
from app.models.catalog import Dataset, DatasetCategory, DatasetStatus, DatasetView
from app.models.notifications import Notification, NotificationType, SupportTicket
from app.models.requests import AccessGrant, GrantStatus
from app.models.user import User
from app.worker.tasks.notifications import process_pending_deletions
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _researcher_headers(client, email: str = "me-researcher@example.com") -> dict:
    token = await register_verified_user(client, email=email)
    return {"Authorization": f"Bearer {token}"}


async def _seed_published_dataset(code: str = "BD-ME-TEST") -> str:
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name=f"Cat-{code}", description="test", color_tag="cat-Environmental")
        db.add(category)
        await db.flush()
        dataset = Dataset(
            code=code,
            title="Me Test Dataset",
            description="test",
            category_id=category.id,
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.commit()
        await db.refresh(dataset)
        return str(dataset.id)


class TestOverview:
    async def test_overview_counts_match_seeded_data(self, client):
        dataset_id = await _seed_published_dataset(code="BD-OV-1")
        headers = await _researcher_headers(client, email="ov-1@example.com")

        r = await client.get(f"/api/v1/catalog/{dataset_id}", headers=headers)
        assert r.status_code == 200

        r = await client.get("/api/v1/me/overview", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pending_requests"] == 0
        assert body["approved_requests"] == 0
        assert body["total_downloads"] == 0
        assert body["datasets_viewed"] == 1
        assert body["total_extracted_bytes"] == 0
        assert body["recent_activity"] == []

    async def test_overview_requires_auth(self, client):
        r = await client.get("/api/v1/me/overview")
        assert r.status_code == 401


class TestProfile:
    async def test_get_profile(self, client):
        headers = await _researcher_headers(client, email="profile-1@example.com")
        r = await client.get("/api/v1/me/profile", headers=headers)
        assert r.status_code == 200
        assert r.json()["email"] == "profile-1@example.com"

    async def test_update_profile_persists(self, client):
        headers = await _researcher_headers(client, email="profile-2@example.com")
        r = await client.patch(
            "/api/v1/me/profile",
            json={"institution": "BORI", "research_area": "Oceanography", "bio": "Hello"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["institution"] == "BORI"

        r2 = await client.get("/api/v1/me/profile", headers=headers)
        assert r2.json()["research_area"] == "Oceanography"
        assert r2.json()["bio"] == "Hello"

    async def test_upload_avatar_produces_storage_key_not_base64(self, client):
        headers = await _researcher_headers(client, email="profile-3@example.com")
        files = {"file": ("avatar.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 100), "image/png")}
        r = await client.post("/api/v1/me/profile/avatar", files=files, headers=headers)
        assert r.status_code == 200, r.text
        key = r.json()["avatar_key"]
        assert key.startswith("avatars/")
        assert not key.startswith("data:")

    async def test_upload_avatar_rejects_bad_extension(self, client):
        headers = await _researcher_headers(client, email="profile-4@example.com")
        files = {"file": ("malware.exe", io.BytesIO(b"x" * 10), "application/octet-stream")}
        r = await client.post("/api/v1/me/profile/avatar", files=files, headers=headers)
        assert r.status_code == 400

    async def test_request_and_cancel_deletion(self, client):
        headers = await _researcher_headers(client, email="profile-5@example.com")
        r = await client.post("/api/v1/me/request-deletion", headers=headers)
        assert r.status_code == 200
        assert r.json()["deletion_requested_at"] is not None

        r2 = await client.post("/api/v1/me/cancel-deletion", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["deletion_requested_at"] is None


class TestSecurity:
    async def test_change_password_revokes_other_sessions(self, client):
        email = "sec-1@example.com"
        headers = await _researcher_headers(client, email=email)

        login2 = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "testpass123"}
        )
        refresh_token_2 = login2.json()["tokens"]["refresh_token"]

        r = await client.post(
            "/api/v1/me/change-password",
            json={"current_password": "testpass123", "new_password": "newpass456"},
            headers=headers,
        )
        assert r.status_code == 204, r.text

        r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token_2})
        assert r2.status_code == 401

        r3 = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "newpass456"}
        )
        assert r3.status_code == 200

    async def test_list_sessions_flags_current(self, client):
        headers = await _researcher_headers(client, email="sec-2@example.com")
        r = await client.get("/api/v1/me/sessions", headers=headers)
        assert r.status_code == 200, r.text
        sessions = r.json()
        assert len(sessions) == 1
        assert sessions[0]["is_current"] is True

    async def test_revoke_session(self, client):
        email = "sec-3@example.com"
        headers = await _researcher_headers(client, email=email)

        login2 = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "testpass123"}
        )
        refresh_token_2 = login2.json()["tokens"]["refresh_token"]

        r = await client.get("/api/v1/me/sessions", headers=headers)
        sessions = r.json()
        assert len(sessions) == 2
        other_session = next(s for s in sessions if not s["is_current"])

        r2 = await client.delete(f"/api/v1/me/sessions/{other_session['id']}", headers=headers)
        assert r2.status_code == 204

        r3 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token_2})
        assert r3.status_code == 401

    async def test_revoke_other_users_session_404s(self, client):
        headers_a = await _researcher_headers(client, email="sec-4a@example.com")
        headers_b = await _researcher_headers(client, email="sec-4b@example.com")

        r = await client.get("/api/v1/me/sessions", headers=headers_b)
        session_id_b = r.json()[0]["id"]

        r2 = await client.delete(f"/api/v1/me/sessions/{session_id_b}", headers=headers_a)
        assert r2.status_code == 404


class TestDeletionGracePeriod:
    async def test_beat_task_revokes_grants_past_grace_period(self, client):
        dataset_id = await _seed_published_dataset(code="BD-DEL-1")
        headers = await _researcher_headers(client, email="del-1@example.com")

        r = await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": "x" * 60},
            headers=headers,
        )
        request_id = r.json()["id"]

        admin_token = await register_verified_user(
            client, email="del-admin@example.com", admin=True, permissions=["Approve Requests"]
        )
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        approve_resp = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        grant_id = approve_resp.json()["id"]

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "del-1@example.com"))
            ).scalar_one()
            user.deletion_requested_at = datetime.now(UTC) - timedelta(days=31)
            await db.commit()

        process_pending_deletions()

        async with AsyncSessionLocal() as db:
            grant = await db.get(AccessGrant, uuid.UUID(grant_id))
            assert grant.status == GrantStatus.REVOKED.value

    async def test_beat_task_ignores_users_within_grace_period(self, client):
        dataset_id = await _seed_published_dataset(code="BD-DEL-2")
        headers = await _researcher_headers(client, email="del-2@example.com")

        r = await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": "x" * 60},
            headers=headers,
        )
        request_id = r.json()["id"]

        admin_token = await register_verified_user(
            client, email="del-admin2@example.com", admin=True, permissions=["Approve Requests"]
        )
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        approve_resp = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        grant_id = approve_resp.json()["id"]

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "del-2@example.com"))
            ).scalar_one()
            user.deletion_requested_at = datetime.now(UTC) - timedelta(days=5)
            await db.commit()

        process_pending_deletions()

        async with AsyncSessionLocal() as db:
            grant = await db.get(AccessGrant, uuid.UUID(grant_id))
            assert grant.status == GrantStatus.ACTIVE.value


class TestPreferences:
    async def test_get_preferences_provisions_defaults(self, client):
        headers = await _researcher_headers(client, email="pref-1@example.com")
        r = await client.get("/api/v1/me/preferences", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["date_format"] == "iso"
        assert body["notify_request_status"] is True

    async def test_update_preferences_persists(self, client):
        headers = await _researcher_headers(client, email="pref-2@example.com")
        r = await client.patch(
            "/api/v1/me/preferences",
            json={
                "notify_request_status": False,
                "notify_new_dataset": False,
                "notify_weekly_digest": True,
                "notify_security_alerts": True,
                "notify_newsletter": True,
                "date_format": "us",
                "coordinate_format": "dms",
                "compact_table_rows": True,
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["date_format"] == "us"

        r2 = await client.get("/api/v1/me/preferences", headers=headers)
        assert r2.json()["coordinate_format"] == "dms"
        assert r2.json()["compact_table_rows"] is True


class TestNotifications:
    async def test_mark_read_and_unread_count(self, client):
        headers = await _researcher_headers(client, email="notif-1@example.com")

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "notif-1@example.com"))
            ).scalar_one()
            db.add(
                Notification(
                    user_id=user.id, type=NotificationType.INFO.value, title="Test", description="x"
                )
            )
            db.add(
                Notification(
                    user_id=user.id, type=NotificationType.INFO.value, title="Test2", description="y"
                )
            )
            await db.commit()

        r = await client.get("/api/v1/me/notifications/unread-count", headers=headers)
        assert r.json()["unread_count"] == 2

        r2 = await client.get("/api/v1/me/notifications", headers=headers)
        notif_id = r2.json()[0]["id"]

        r3 = await client.patch(f"/api/v1/me/notifications/{notif_id}/read", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["unread"] is False

        r4 = await client.get("/api/v1/me/notifications/unread-count", headers=headers)
        assert r4.json()["unread_count"] == 1

        r5 = await client.post("/api/v1/me/notifications/read-all", headers=headers)
        assert r5.status_code == 204

        r6 = await client.get("/api/v1/me/notifications/unread-count", headers=headers)
        assert r6.json()["unread_count"] == 0

    async def test_notifications_scoped_to_user(self, client):
        headers_a = await _researcher_headers(client, email="notif-2a@example.com")
        headers_b = await _researcher_headers(client, email="notif-2b@example.com")

        async with AsyncSessionLocal() as db:
            user_a = (
                await db.execute(select(User).where(User.email == "notif-2a@example.com"))
            ).scalar_one()
            db.add(Notification(user_id=user_a.id, type=NotificationType.INFO.value, title="A"))
            await db.commit()

        r_a = await client.get("/api/v1/me/notifications", headers=headers_a)
        r_b = await client.get("/api/v1/me/notifications", headers=headers_b)
        assert len(r_a.json()) == 1
        assert len(r_b.json()) == 0


class TestSupportTickets:
    async def test_create_and_list_ticket(self, client):
        headers = await _researcher_headers(client, email="support-1@example.com")
        r = await client.post(
            "/api/v1/me/support-tickets",
            json={"subject": "Cannot download", "category": "technical", "message": "Help please"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "open"

        r2 = await client.get("/api/v1/me/support-tickets", headers=headers)
        assert len(r2.json()) == 1
        assert r2.json()[0]["subject"] == "Cannot download"

    async def test_ticket_dispatches_email_task_dev_mode_noop(self, client):
        headers = await _researcher_headers(client, email="support-2@example.com")
        r = await client.post(
            "/api/v1/me/support-tickets",
            json={"subject": "Question", "message": "How does this work?"},
            headers=headers,
        )
        assert r.status_code == 201

        async with AsyncSessionLocal() as db:
            tickets = (await db.execute(select(SupportTicket))).scalars().all()
            assert len(tickets) == 1


class TestCmsBlocks:
    async def test_get_blocks_returns_seeded_content(self, client):
        async with AsyncSessionLocal() as db:
            db.add(CmsBlock(key="dashboard-help-test-1", page="dashboard-help", label="Q", value="A"))
            await db.commit()

        headers = await _researcher_headers(client, email="cms-1@example.com")
        r = await client.get("/api/v1/me/cms-blocks?page=dashboard-help", headers=headers)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["value"] == "A"

    async def test_get_blocks_uses_cache_on_second_call(self, client):
        async with AsyncSessionLocal() as db:
            db.add(CmsBlock(key="dashboard-help-test-2", page="dashboard-help", label="Q2", value="A2"))
            await db.commit()

        headers = await _researcher_headers(client, email="cms-2@example.com")
        r1 = await client.get("/api/v1/me/cms-blocks?page=dashboard-help", headers=headers)
        assert len(r1.json()) == 1

        async with AsyncSessionLocal() as db:
            block = await db.get(CmsBlock, "dashboard-help-test-2")
            await db.delete(block)
            await db.commit()

        r2 = await client.get("/api/v1/me/cms-blocks?page=dashboard-help", headers=headers)
        assert len(r2.json()) == 1


class TestDatasetViewTracking:
    async def test_view_logged_for_authenticated_user(self, client):
        dataset_id = await _seed_published_dataset(code="BD-DV-1")
        headers = await _researcher_headers(client, email="dv-1@example.com")

        r = await client.get(f"/api/v1/catalog/{dataset_id}", headers=headers)
        assert r.status_code == 200

        async with AsyncSessionLocal() as db:
            views = (await db.execute(select(DatasetView))).scalars().all()
            assert len(views) == 1
            assert str(views[0].dataset_id) == dataset_id

    async def test_view_not_logged_for_anonymous_caller(self, client):
        dataset_id = await _seed_published_dataset(code="BD-DV-2")

        r = await client.get(f"/api/v1/catalog/{dataset_id}")
        assert r.status_code == 200

        async with AsyncSessionLocal() as db:
            views = (await db.execute(select(DatasetView))).scalars().all()
            assert len(views) == 0
