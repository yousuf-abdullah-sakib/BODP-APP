import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.notifications import Notification, SupportTicket
from app.models.user import User
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, email: str, permissions: list[str]) -> dict:
    token = await register_verified_user(client, email=email, admin=True, permissions=permissions)
    return {"Authorization": f"Bearer {token}"}


async def _submitter_headers(client, *, email: str = "ticket-submitter@example.com") -> dict:
    token = await register_verified_user(client, email=email)
    return {"Authorization": f"Bearer {token}"}


async def _submit_ticket(client, headers, **overrides) -> dict:
    data = {
        "subject": "Cannot download dataset",
        "category": "Technical",
        "priority": "medium",
        "message": "The download button does nothing when I click it.",
    }
    data.update(overrides)
    return await client.post("/api/v1/me/support-tickets", json=data, headers=headers)


class TestTicketCreation:
    async def test_create_still_works(self, client):
        headers = await _submitter_headers(client)
        r = await _submit_ticket(client, headers)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["subject"] == "Cannot download dataset"
        assert body["status"] == "open"
        assert body["reply_message"] is None
        assert body["replied_at"] is None

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(SupportTicket))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "open"

    async def test_create_requires_auth(self, client):
        r = await _submit_ticket(client, {})
        assert r.status_code == 401

    async def test_self_list_scoped_to_user(self, client):
        headers_a = await _submitter_headers(client, email="ticket-a@example.com")
        headers_b = await _submitter_headers(client, email="ticket-b@example.com")
        await _submit_ticket(client, headers_a)

        r_a = await client.get("/api/v1/me/support-tickets", headers=headers_a)
        r_b = await client.get("/api/v1/me/support-tickets", headers=headers_b)
        assert len(r_a.json()) == 1
        assert len(r_b.json()) == 0

    async def test_create_notifies_only_manage_support_admins(self, client):
        # Same both-admin-types setup as the contact form tests: seed one
        # admin WITH "Manage Support" and one admin WITHOUT it. Ticket
        # creation now ALSO calls notify_admins_with_permission (new this
        # session) in addition to its pre-existing best-effort
        # send_support_ticket_created Celery email — only the permissioned
        # admin should get an in-app Notification row.
        support_headers = await _admin_headers(
            client, email="support-ticket-support-admin@example.com", permissions=["Manage Support"]
        )
        plain_headers = await _admin_headers(
            client, email="support-ticket-plain-admin@example.com", permissions=["Edit Datasets"]
        )
        assert support_headers and plain_headers

        submitter_headers = await _submitter_headers(client)
        r = await _submit_ticket(client, submitter_headers)
        assert r.status_code == 201, r.text

        async with AsyncSessionLocal() as db:
            support_admin = (
                await db.execute(
                    select(User).where(User.email == "support-ticket-support-admin@example.com")
                )
            ).scalar_one()
            plain_admin = (
                await db.execute(
                    select(User).where(User.email == "support-ticket-plain-admin@example.com")
                )
            ).scalar_one()

            support_notifs = (
                await db.execute(
                    select(Notification).where(
                        Notification.user_id == support_admin.id,
                        Notification.title == "New support ticket",
                    )
                )
            ).scalars().all()
            # Filtered by title, not a bare user_id match — registering the
            # support/plain admins and the submitter each fire their own
            # unrelated "New user registered" broadcast (notify_admins,
            # coarse role=='admin') to EVERY admin including plain_admin.
            # That's correct, pre-existing, unrelated behavior; this
            # assertion is specifically about the "New support ticket"
            # notification introduced this session, which must NOT reach
            # plain_admin.
            plain_notifs = (
                await db.execute(
                    select(Notification).where(
                        Notification.user_id == plain_admin.id,
                        Notification.title == "New support ticket",
                    )
                )
            ).scalars().all()

        assert len(support_notifs) == 1
        assert "Cannot download dataset" in support_notifs[0].description
        assert plain_notifs == []


class TestAdminListAndDetail:
    async def test_list_requires_manage_support_permission(self, client):
        headers = await _admin_headers(
            client, email="support-list-noperm-admin@example.com", permissions=["Edit Datasets"]
        )
        r = await client.get("/api/v1/admin/support-tickets", headers=headers)
        assert r.status_code == 403

    async def test_list_non_admin_403s(self, client):
        headers = await _submitter_headers(client, email="support-rando@example.com")
        r = await client.get("/api/v1/admin/support-tickets", headers=headers)
        assert r.status_code == 403

    async def test_list_requires_auth(self, client):
        r = await client.get("/api/v1/admin/support-tickets")
        assert r.status_code == 401

    async def test_list_returns_requester_identity(self, client):
        submitter_headers = await _submitter_headers(client)
        await _submit_ticket(client, submitter_headers)

        headers = await _admin_headers(
            client, email="support-list-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get("/api/v1/admin/support-tickets", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["requester_email"] == "ticket-submitter@example.com"
        assert body[0]["requester_name"] == "Fixture User"
        assert body[0]["status"] == "open"

    async def test_list_filters_by_status(self, client):
        submitter_headers = await _submitter_headers(client)
        await _submit_ticket(client, submitter_headers)

        headers = await _admin_headers(
            client, email="support-filter-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get("/api/v1/admin/support-tickets?status_filter=answered", headers=headers)
        assert r.status_code == 200
        assert r.json() == []

        r = await client.get("/api/v1/admin/support-tickets?status_filter=open", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_list_search_matches_subject(self, client):
        submitter_headers = await _submitter_headers(client)
        await _submit_ticket(client, submitter_headers)

        headers = await _admin_headers(
            client, email="support-search-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get("/api/v1/admin/support-tickets?search=download", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 1

        r = await client.get("/api/v1/admin/support-tickets?search=nomatch", headers=headers)
        assert r.status_code == 200
        assert r.json() == []

    async def test_get_detail_requires_permission(self, client):
        submitter_headers = await _submitter_headers(client)
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        headers = await _admin_headers(
            client, email="support-detail-noperm-admin@example.com", permissions=["Edit Datasets"]
        )
        r = await client.get(f"/api/v1/admin/support-tickets/{ticket_id}", headers=headers)
        assert r.status_code == 403

    async def test_get_detail_returns_full_message(self, client):
        submitter_headers = await _submitter_headers(client)
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        headers = await _admin_headers(
            client, email="support-detail-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get(f"/api/v1/admin/support-tickets/{ticket_id}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["message"] == "The download button does nothing when I click it."
        assert body["requester_email"] == "ticket-submitter@example.com"
        assert body["reply_message"] is None
        assert body["replied_by_name"] is None

    async def test_get_detail_missing_404s(self, client):
        import uuid

        headers = await _admin_headers(
            client, email="support-detail-404-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get(f"/api/v1/admin/support-tickets/{uuid.uuid4()}", headers=headers)
        assert r.status_code == 404


class TestReplyToTicket:
    async def test_reply_requires_permission(self, client):
        submitter_headers = await _submitter_headers(client)
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        headers = await _admin_headers(
            client, email="support-reply-noperm-admin@example.com", permissions=["Edit Datasets"]
        )
        r = await client.post(
            f"/api/v1/admin/support-tickets/{ticket_id}/reply",
            json={"reply_message": "Try clearing your browser cache."},
            headers=headers,
        )
        assert r.status_code == 403

    async def test_reply_sets_status_answered_and_persists_fields(self, client):
        submitter_headers = await _submitter_headers(client)
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        headers = await _admin_headers(
            client, email="support-replier@example.com", permissions=["Manage Support"]
        )
        r = await client.post(
            f"/api/v1/admin/support-tickets/{ticket_id}/reply",
            json={"reply_message": "This is now fixed, please retry."},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "answered"
        assert body["reply_message"] == "This is now fixed, please retry."
        assert body["replied_by_name"] == "Fixture User"
        assert body["replied_at"] is not None

        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(SupportTicket))).scalar_one()
            replier = (
                await db.execute(select(User).where(User.email == "support-replier@example.com"))
            ).scalar_one()
        assert row.status == "answered"
        assert row.reply_message == "This is now fixed, please retry."
        assert row.replied_by_id == replier.id
        assert row.replied_at is not None

    async def test_reply_creates_notification_for_submitter_not_admin(self, client):
        submitter_headers = await _submitter_headers(client, email="notif-submitter@example.com")
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers(
            client, email="support-notify-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.post(
            f"/api/v1/admin/support-tickets/{ticket_id}/reply",
            json={"reply_message": "Here is your answer."},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text

        # Assert via the submitter's own GET /me/notifications, exercising
        # the real read path a user would see.
        r = await client.get("/api/v1/me/notifications", headers=submitter_headers)
        assert r.status_code == 200
        titles = [n["title"] for n in r.json()]
        assert "Your support ticket was answered" in titles

        # Also confirm the admin who replied did NOT get a notification for
        # their own action, and confirm via direct DB query on user_id.
        async with AsyncSessionLocal() as db:
            submitter = (
                await db.execute(select(User).where(User.email == "notif-submitter@example.com"))
            ).scalar_one()
            admin = (
                await db.execute(select(User).where(User.email == "support-notify-admin@example.com"))
            ).scalar_one()

            submitter_notifs = (
                await db.execute(
                    select(Notification).where(
                        Notification.user_id == submitter.id,
                        Notification.title == "Your support ticket was answered",
                    )
                )
            ).scalars().all()
            admin_notifs_for_reply = (
                await db.execute(
                    select(Notification).where(
                        Notification.user_id == admin.id,
                        Notification.title == "Your support ticket was answered",
                    )
                )
            ).scalars().all()

        assert len(submitter_notifs) == 1
        assert "Cannot download dataset" in submitter_notifs[0].description
        assert admin_notifs_for_reply == []

    async def test_reply_sends_no_email(self, client, monkeypatch):
        # Deliberate product decision: support ticket replies surface only
        # as an in-app Notification (see admin_support_service.reply_to_ticket's
        # comment), unlike contact-submission replies which do email the
        # submitter. Assert EmailService.send is never invoked for this path.
        from app.services.email_service import EmailService

        send_calls = []
        monkeypatch.setattr(
            EmailService, "send", lambda self, *a, **k: send_calls.append((a, k))
        )

        submitter_headers = await _submitter_headers(client, email="no-email-submitter@example.com")
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers(
            client, email="support-no-email-admin@example.com", permissions=["Manage Support"]
        )
        # Ticket creation (send_support_ticket_created Celery task) and both
        # users' own registration each trigger their own EmailService.send
        # calls (verification emails, best-effort ticket-created email) —
        # unrelated to the reply path and unchanged this session. Clear
        # after all setup/registration is done so only calls made by the
        # reply itself remain.
        send_calls.clear()
        r = await client.post(
            f"/api/v1/admin/support-tickets/{ticket_id}/reply",
            json={"reply_message": "Fixed, no email needed."},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert send_calls == []

    async def test_reply_writes_audit_log(self, client):
        from app.models.audit import AuditLogEntry

        submitter_headers = await _submitter_headers(client)
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        headers = await _admin_headers(
            client, email="support-audit-admin@example.com", permissions=["Manage Support"]
        )
        await client.post(
            f"/api/v1/admin/support-tickets/{ticket_id}/reply",
            json={"reply_message": "Answering now."},
            headers=headers,
        )

        async with AsyncSessionLocal() as db:
            entries = (await db.execute(select(AuditLogEntry))).scalars().all()
        assert any(e.action == "Replied to support ticket" for e in entries)

    async def test_reply_empty_message_rejected(self, client):
        submitter_headers = await _submitter_headers(client)
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        headers = await _admin_headers(
            client, email="support-empty-reply-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.post(
            f"/api/v1/admin/support-tickets/{ticket_id}/reply",
            json={"reply_message": ""},
            headers=headers,
        )
        assert r.status_code == 422

    async def test_reply_missing_ticket_404s(self, client):
        import uuid

        headers = await _admin_headers(
            client, email="support-reply-404-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.post(
            f"/api/v1/admin/support-tickets/{uuid.uuid4()}/reply",
            json={"reply_message": "Hello"},
            headers=headers,
        )
        assert r.status_code == 404

    async def test_self_service_dashboard_reflects_reply(self, client):
        # The whole point of storing reply_message/replied_at on the ticket
        # row (not just returning it from the admin endpoint) is so the
        # user's own dashboard (GET /me/support-tickets) can show it.
        submitter_headers = await _submitter_headers(client, email="dashboard-submitter@example.com")
        submit_resp = await _submit_ticket(client, submitter_headers)
        ticket_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers(
            client, email="support-dashboard-admin@example.com", permissions=["Manage Support"]
        )
        await client.post(
            f"/api/v1/admin/support-tickets/{ticket_id}/reply",
            json={"reply_message": "Resolved — see attached steps."},
            headers=admin_headers,
        )

        r = await client.get("/api/v1/me/support-tickets", headers=submitter_headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["status"] == "answered"
        assert body[0]["reply_message"] == "Resolved — see attached steps."
        assert body[0]["replied_at"] is not None
