import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.notifications import ContactSubmission, Notification
from app.models.user import User
from app.services import email_service as email_service_module
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio

_LONG_MESSAGE = "This is a message that is definitely at least twenty characters long."


async def _admin_headers(client, *, email: str, permissions: list[str]) -> dict:
    token = await register_verified_user(client, email=email, admin=True, permissions=permissions)
    return {"Authorization": f"Bearer {token}"}


def _capture_reply_email(monkeypatch) -> list[dict]:
    captured = []

    def _fake_send(self, to, *, name, original_subject, original_message, reply_message):
        captured.append(
            {
                "to": to,
                "name": name,
                "original_subject": original_subject,
                "original_message": original_message,
                "reply_message": reply_message,
            }
        )

    monkeypatch.setattr(email_service_module.EmailService, "send_contact_reply_email", _fake_send)
    return captured


async def _submit(client, **overrides) -> dict:
    data = {
        "name": "Jane Researcher",
        "email": "jane@example.com",
        "organization": "Ocean Institute",
        "subject": "Question about data access",
        "message": _LONG_MESSAGE,
    }
    data.update(overrides)
    return await client.post("/api/v1/content/contact", json=data)


class TestPublicSubmission:
    async def test_submit_persists_row(self, client):
        r = await _submit(client)
        assert r.status_code == 201, r.text
        assert "message" in r.json()

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(ContactSubmission))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.name == "Jane Researcher"
        assert row.email == "jane@example.com"
        assert row.organization == "Ocean Institute"
        assert row.subject == "Question about data access"
        assert row.status == "new"
        assert row.reply_message is None
        assert row.replied_by_id is None
        assert row.replied_at is None

    async def test_organization_optional(self, client):
        r = await _submit(client, organization=None)
        assert r.status_code == 201, r.text

        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(ContactSubmission))).scalar_one()
        assert row.organization is None

    async def test_message_too_short_rejected(self, client):
        r = await _submit(client, message="too short")
        assert r.status_code == 422

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(ContactSubmission))).scalars().all()
        assert rows == []

    async def test_invalid_email_rejected(self, client):
        r = await _submit(client, email="not-an-email")
        assert r.status_code == 422

    async def test_submit_notifies_only_manage_support_admins(self, client):
        # Seed one admin WITH "Manage Support" and one admin WITHOUT it —
        # notify_admins_with_permission must select by the fine-grained
        # permission, not a coarse role=='admin' broadcast (that mixing is
        # exactly the double-notification bug documented in
        # admin_notify_service.notify_admins' docstring, fixed earlier this
        # session for the analogous request-submission path).
        support_headers = await _admin_headers(
            client, email="contact-support-admin@example.com", permissions=["Manage Support"]
        )
        plain_headers = await _admin_headers(
            client, email="contact-plain-admin@example.com", permissions=["Edit Datasets"]
        )
        assert support_headers and plain_headers  # created, not otherwise used

        r = await _submit(client)
        assert r.status_code == 201, r.text

        async with AsyncSessionLocal() as db:
            support_admin = (
                await db.execute(
                    select(User).where(User.email == "contact-support-admin@example.com")
                )
            ).scalar_one()
            plain_admin = (
                await db.execute(select(User).where(User.email == "contact-plain-admin@example.com"))
            ).scalar_one()

            support_notifs = (
                await db.execute(
                    select(Notification).where(
                        Notification.user_id == support_admin.id,
                        Notification.title == "New contact form submission",
                    )
                )
            ).scalars().all()
            plain_notifs = (
                await db.execute(
                    select(Notification).where(Notification.user_id == plain_admin.id)
                )
            ).scalars().all()

        assert len(support_notifs) == 1
        assert "Jane Researcher" in support_notifs[0].description
        assert "jane@example.com" in support_notifs[0].description
        assert plain_notifs == []

    async def test_submit_notifies_one_row_per_manage_support_admin(self, client):
        # Two separate admins both holding "Manage Support" should each get
        # their own row — not a single shared/broadcast notification.
        headers_a = await _admin_headers(
            client, email="contact-support-a@example.com", permissions=["Manage Support"]
        )
        headers_b = await _admin_headers(
            client, email="contact-support-b@example.com", permissions=["Manage Support"]
        )
        assert headers_a and headers_b

        r = await _submit(client, email="second@example.com")
        assert r.status_code == 201, r.text

        async with AsyncSessionLocal() as db:
            admins = (
                await db.execute(
                    select(User).where(
                        User.email.in_(
                            ["contact-support-a@example.com", "contact-support-b@example.com"]
                        )
                    )
                )
            ).scalars().all()
            admin_ids = {u.id for u in admins}
            notifs = (
                await db.execute(
                    select(Notification).where(
                        Notification.user_id.in_(admin_ids),
                        Notification.title == "New contact form submission",
                    )
                )
            ).scalars().all()

        assert len(notifs) == 2


class TestAdminListAndDetail:
    async def test_list_requires_manage_support_permission(self, client):
        headers = await _admin_headers(
            client, email="contact-noperm-admin@example.com", permissions=["Edit Datasets"]
        )
        r = await client.get("/api/v1/admin/contact", headers=headers)
        assert r.status_code == 403

    async def test_list_non_admin_403s(self, client):
        token = await register_verified_user(client, email="contact-rando@example.com")
        r = await client.get(
            "/api/v1/admin/contact", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    async def test_list_requires_auth(self, client):
        r = await client.get("/api/v1/admin/contact")
        assert r.status_code == 401

    async def test_list_returns_submission(self, client):
        await _submit(client)
        headers = await _admin_headers(
            client, email="contact-list-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get("/api/v1/admin/contact", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["email"] == "jane@example.com"
        assert body[0]["status"] == "new"

    async def test_list_filters_by_status(self, client):
        await _submit(client)
        headers = await _admin_headers(
            client, email="contact-filter-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get("/api/v1/admin/contact?status_filter=replied", headers=headers)
        assert r.status_code == 200
        assert r.json() == []

        r = await client.get("/api/v1/admin/contact?status_filter=new", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_list_search_matches_name_email_subject(self, client):
        await _submit(client)
        headers = await _admin_headers(
            client, email="contact-search-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get("/api/v1/admin/contact?search=Jane", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 1

        r = await client.get("/api/v1/admin/contact?search=nomatch", headers=headers)
        assert r.status_code == 200
        assert r.json() == []

    async def test_get_detail_requires_permission(self, client):
        r = await _submit(client)
        assert r.status_code == 201

        async with AsyncSessionLocal() as db:
            submission_id = str((await db.execute(select(ContactSubmission))).scalar_one().id)

        headers = await _admin_headers(
            client, email="contact-detail-noperm@example.com", permissions=["Edit Datasets"]
        )
        r = await client.get(f"/api/v1/admin/contact/{submission_id}", headers=headers)
        assert r.status_code == 403

    async def test_get_detail_returns_full_message(self, client):
        await _submit(client)
        async with AsyncSessionLocal() as db:
            submission_id = str((await db.execute(select(ContactSubmission))).scalar_one().id)

        headers = await _admin_headers(
            client, email="contact-detail-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get(f"/api/v1/admin/contact/{submission_id}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["message"] == _LONG_MESSAGE
        assert body["reply_message"] is None
        assert body["replied_by_name"] is None
        assert body["replied_at"] is None

    async def test_get_detail_missing_404s(self, client):
        import uuid

        headers = await _admin_headers(
            client, email="contact-detail-404-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.get(f"/api/v1/admin/contact/{uuid.uuid4()}", headers=headers)
        assert r.status_code == 404


class TestReplyToSubmission:
    async def test_reply_requires_permission(self, client, monkeypatch):
        _capture_reply_email(monkeypatch)
        await _submit(client)
        async with AsyncSessionLocal() as db:
            submission_id = str((await db.execute(select(ContactSubmission))).scalar_one().id)

        headers = await _admin_headers(
            client, email="contact-reply-noperm@example.com", permissions=["Edit Datasets"]
        )
        r = await client.post(
            f"/api/v1/admin/contact/{submission_id}/reply",
            json={"reply_message": "Thanks for reaching out."},
            headers=headers,
        )
        assert r.status_code == 403

    async def test_reply_sets_status_and_persists_fields(self, client, monkeypatch):
        captured = _capture_reply_email(monkeypatch)
        await _submit(client)
        async with AsyncSessionLocal() as db:
            submission_id = str((await db.execute(select(ContactSubmission))).scalar_one().id)

        headers = await _admin_headers(
            client, email="contact-replier@example.com", permissions=["Manage Support"]
        )
        r = await client.post(
            f"/api/v1/admin/contact/{submission_id}/reply",
            json={"reply_message": "Here is the info you requested."},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "replied"
        assert body["reply_message"] == "Here is the info you requested."
        assert body["replied_by_name"] == "Fixture User"
        assert body["replied_at"] is not None

        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(ContactSubmission))).scalar_one()
            replier = (
                await db.execute(select(User).where(User.email == "contact-replier@example.com"))
            ).scalar_one()
        assert row.status == "replied"
        assert row.reply_message == "Here is the info you requested."
        assert row.replied_by_id == replier.id
        assert row.replied_at is not None

    async def test_reply_sends_email_to_submitter(self, client, monkeypatch):
        captured = _capture_reply_email(monkeypatch)
        await _submit(client)
        async with AsyncSessionLocal() as db:
            submission_id = str((await db.execute(select(ContactSubmission))).scalar_one().id)

        headers = await _admin_headers(
            client, email="contact-emailer@example.com", permissions=["Manage Support"]
        )
        r = await client.post(
            f"/api/v1/admin/contact/{submission_id}/reply",
            json={"reply_message": "Here is our answer."},
            headers=headers,
        )
        assert r.status_code == 200, r.text

        assert len(captured) == 1
        call = captured[0]
        assert call["to"] == "jane@example.com"
        assert call["name"] == "Jane Researcher"
        assert call["original_subject"] == "Question about data access"
        assert call["original_message"] == _LONG_MESSAGE
        assert call["reply_message"] == "Here is our answer."

    async def test_reply_writes_audit_log(self, client, monkeypatch):
        _capture_reply_email(monkeypatch)
        from app.models.audit import AuditLogEntry

        await _submit(client)
        async with AsyncSessionLocal() as db:
            submission_id = str((await db.execute(select(ContactSubmission))).scalar_one().id)

        headers = await _admin_headers(
            client, email="contact-audit-admin@example.com", permissions=["Manage Support"]
        )
        await client.post(
            f"/api/v1/admin/contact/{submission_id}/reply",
            json={"reply_message": "Answering now."},
            headers=headers,
        )

        async with AsyncSessionLocal() as db:
            entries = (await db.execute(select(AuditLogEntry))).scalars().all()
        assert any(e.action == "Replied to contact submission" for e in entries)

    async def test_reply_empty_message_rejected(self, client, monkeypatch):
        _capture_reply_email(monkeypatch)
        await _submit(client)
        async with AsyncSessionLocal() as db:
            submission_id = str((await db.execute(select(ContactSubmission))).scalar_one().id)

        headers = await _admin_headers(
            client, email="contact-empty-reply-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.post(
            f"/api/v1/admin/contact/{submission_id}/reply",
            json={"reply_message": ""},
            headers=headers,
        )
        assert r.status_code == 422

    async def test_reply_missing_submission_404s(self, client, monkeypatch):
        import uuid

        _capture_reply_email(monkeypatch)
        headers = await _admin_headers(
            client, email="contact-reply-404-admin@example.com", permissions=["Manage Support"]
        )
        r = await client.post(
            f"/api/v1/admin/contact/{uuid.uuid4()}/reply",
            json={"reply_message": "Hello"},
            headers=headers,
        )
        assert r.status_code == 404

    async def test_reply_twice_overwrites_and_sends_second_email(self, client, monkeypatch):
        # Behavior inferred from reading admin_contact_service.reply_to_submission:
        # there is no guard against replying to an already-"replied" submission —
        # it unconditionally overwrites reply_message/replied_by_id/replied_at
        # and re-sends the reply email. Asserting the actual (permissive)
        # behavior here rather than assuming a 409-on-second-reply that the
        # code doesn't implement.
        captured = _capture_reply_email(monkeypatch)
        await _submit(client)
        async with AsyncSessionLocal() as db:
            submission_id = str((await db.execute(select(ContactSubmission))).scalar_one().id)

        headers = await _admin_headers(
            client, email="contact-double-reply-admin@example.com", permissions=["Manage Support"]
        )
        r1 = await client.post(
            f"/api/v1/admin/contact/{submission_id}/reply",
            json={"reply_message": "First reply."},
            headers=headers,
        )
        assert r1.status_code == 200, r1.text

        r2 = await client.post(
            f"/api/v1/admin/contact/{submission_id}/reply",
            json={"reply_message": "Second, corrected reply."},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["reply_message"] == "Second, corrected reply."
        assert r2.json()["status"] == "replied"

        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(ContactSubmission))).scalar_one()
        assert row.reply_message == "Second, corrected reply."

        assert len(captured) == 2
        assert captured[1]["reply_message"] == "Second, corrected reply."
