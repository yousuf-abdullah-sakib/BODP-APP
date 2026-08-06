from datetime import datetime, timezone

import pytest

from app.core.security import TokenType, decode_token
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client) -> dict:
    token = await register_verified_user(
        client, email="settings-admin@example.com", admin=True
    )
    return {"Authorization": f"Bearer {token}"}


class TestGeneralSettings:
    async def test_get_returns_defaults(self, client):
        headers = await _admin_headers(client)
        r = await client.get("/api/v1/admin/settings/general", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["site_name"] == "BODP"
        assert body["session_lifetime_min"] == 1440

    async def test_patch_updates_only_provided_fields(self, client):
        headers = await _admin_headers(client)

        r = await client.patch(
            "/api/v1/admin/settings/general", json={"site_name": "Custom Portal Name"}, headers=headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["site_name"] == "Custom Portal Name"
        # Untouched fields keep their prior values (exclude_unset semantics).
        assert body["max_upload_size_mb"] == 5000
        assert body["session_lifetime_min"] == 1440

        r = await client.patch(
            "/api/v1/admin/settings/general", json={"max_upload_size_mb": 2000}, headers=headers
        )
        assert r.status_code == 200
        body = r.json()
        assert body["max_upload_size_mb"] == 2000
        # Previous PATCH's change must persist, not be reset by this second PATCH.
        assert body["site_name"] == "Custom Portal Name"

    async def test_requires_admin(self, client):
        token = await register_verified_user(client, email="settings-regular@example.com", admin=False)
        headers = {"Authorization": f"Bearer {token}"}
        r = await client.get("/api/v1/admin/settings/general", headers=headers)
        assert r.status_code == 403


class TestNotificationSettings:
    async def test_patch_updates_only_provided_fields(self, client):
        headers = await _admin_headers(client)

        r = await client.get("/api/v1/admin/settings/notifications", headers=headers)
        assert r.status_code == 200
        assert r.json()["notify_new_request"] is True

        r = await client.patch(
            "/api/v1/admin/settings/notifications", json={"notify_new_request": False}, headers=headers
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["notify_new_request"] is False
        # Omitted fields stay unchanged.
        assert body["notify_new_user"] is True
        assert body["notify_expiring_dataset"] is True


class TestSessionLifetimeAffectsIssuedTokens:
    async def test_changed_session_lifetime_reflected_in_fresh_token_exp(self, client):
        headers = await _admin_headers(client)

        r = await client.patch(
            "/api/v1/admin/settings/general", json={"session_lifetime_min": 45}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["session_lifetime_min"] == 45

        # Register a brand new user and log in fresh — the access token this
        # login issues must reflect the newly-configured session lifetime.
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "Session Test User",
                "email": "session-lifetime-test@example.com",
                "password": "testpass123",
                "institution": None,
                "phone": None,
            },
        )
        assert r.status_code == 201

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.core.security import create_email_verification_token
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).where(User.email == "session-lifetime-test@example.com")
            )
            user = result.scalar_one()
            verify_token = create_email_verification_token(str(user.id), user.email)

        r = await client.post("/api/v1/auth/verify-email", json={"token": verify_token})
        assert r.status_code == 200

        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "session-lifetime-test@example.com", "password": "testpass123"},
        )
        assert r.status_code == 200
        access_token = r.json()["tokens"]["access_token"]

        payload = decode_token(access_token, TokenType.ACCESS)
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        actual_lifetime_min = (exp - iat).total_seconds() / 60

        assert actual_lifetime_min == pytest.approx(45, abs=0.1)
