import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_email_verification_token
from app.models.audit import ActiveSession
from app.models.user import User

pytestmark = pytest.mark.asyncio

REGISTER_PAYLOAD = {
    "full_name": "Dr. Test Researcher",
    "email": "researcher@example.com",
    "password": "correcthorse123",
    "institution": "Test University",
    "phone": None,
}


async def _register_and_verify(client) -> None:
    r = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
    assert r.status_code == 201

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == REGISTER_PAYLOAD["email"]))
        user = result.scalar_one()
        token = create_email_verification_token(str(user.id), user.email)

    r = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200


class TestRegistration:
    async def test_register_creates_unverified_user(self, client):
        r = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert r.status_code == 201
        body = r.json()
        assert body["email"] == REGISTER_PAYLOAD["email"]

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == REGISTER_PAYLOAD["email"]))
            user = result.scalar_one()
            assert user.email_verified_at is None
            assert user.role == "user"
            assert user.password_hash != REGISTER_PAYLOAD["password"]

    async def test_register_duplicate_email_rejected(self, client):
        r1 = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert r1.status_code == 201
        r2 = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert r2.status_code == 409

    async def test_register_weak_password_rejected(self, client):
        payload = {**REGISTER_PAYLOAD, "password": "short"}
        r = await client.post("/api/v1/auth/register", json=payload)
        assert r.status_code == 422

    async def test_register_password_without_digit_rejected(self, client):
        payload = {**REGISTER_PAYLOAD, "password": "onlyletters"}
        r = await client.post("/api/v1/auth/register", json=payload)
        assert r.status_code == 422


class TestEmailVerification:
    async def test_verify_with_valid_token(self, client):
        r = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert r.status_code == 201

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == REGISTER_PAYLOAD["email"]))
            user = result.scalar_one()
            token = create_email_verification_token(str(user.id), user.email)

        r = await client.post("/api/v1/auth/verify-email", json={"token": token})
        assert r.status_code == 200

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == REGISTER_PAYLOAD["email"]))
            user = result.scalar_one()
            assert user.email_verified_at is not None

    async def test_verify_with_garbage_token_rejected(self, client):
        r = await client.post("/api/v1/auth/verify-email", json={"token": "not-a-real-token"})
        assert r.status_code == 400

    async def test_verify_with_access_token_type_rejected(self, client):
        """An access token must not be usable as an email-verification token."""
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        access_token = r.json()["tokens"]["access_token"]
        r = await client.post("/api/v1/auth/verify-email", json={"token": access_token})
        assert r.status_code == 400


class TestLogin:
    async def test_login_before_verification_rejected(self, client):
        r = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert r.status_code == 201
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        assert r.status_code == 403

    async def test_login_after_verification_succeeds(self, client):
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["email"] == REGISTER_PAYLOAD["email"]
        assert body["user"]["role"] == "user"
        assert "access_token" in body["tokens"]
        assert "refresh_token" in body["tokens"]

    async def test_login_wrong_password_rejected(self, client):
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": "wrong-password-123"},
        )
        assert r.status_code == 401

    async def test_login_unknown_email_rejected(self, client):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "whatever123"},
        )
        assert r.status_code == 401

    async def test_login_lockout_after_repeated_failures(self, client):
        await _register_and_verify(client)
        for _ in range(5):
            r = await client.post(
                "/api/v1/auth/login",
                json={"email": REGISTER_PAYLOAD["email"], "password": "wrong-password"},
            )
            assert r.status_code == 401

        # 6th attempt (even with the CORRECT password) should now be locked out.
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        assert r.status_code == 429

    async def test_suspended_account_cannot_login(self, client):
        await _register_and_verify(client)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == REGISTER_PAYLOAD["email"]))
            user = result.scalar_one()
            user.status = "suspended"
            await db.commit()

        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        assert r.status_code == 403


class TestTokenLifecycle:
    async def test_me_requires_bearer_token(self, client):
        r = await client.get("/api/v1/auth/me")
        assert r.status_code == 401

    async def test_me_with_valid_access_token(self, client):
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        access_token = r.json()["tokens"]["access_token"]
        r = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert r.status_code == 200
        assert r.json()["email"] == REGISTER_PAYLOAD["email"]

    async def test_me_with_tampered_token_rejected(self, client):
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        access_token = r.json()["tokens"]["access_token"]
        tampered = access_token[:-4] + "abcd"
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"})
        assert r.status_code == 401

    async def test_refresh_issues_new_access_token(self, client):
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        tokens = r.json()["tokens"]
        r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 200
        assert r.json()["access_token"] != tokens["access_token"]

    async def test_refresh_with_access_token_rejected(self, client):
        """An access token must not work where a refresh token is expected."""
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        access_token = r.json()["tokens"]["access_token"]
        r = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
        assert r.status_code == 401

    async def test_logout_revokes_session(self, client):
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        tokens = r.json()["tokens"]

        r = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 204

        r = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert r.status_code == 401

    async def test_login_creates_active_session_row(self, client):
        await _register_and_verify(client)
        await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(ActiveSession))
            sessions = result.scalars().all()
            assert len(sessions) == 1
            assert sessions[0].revoked_at is None


class TestPasswordChange:
    async def test_change_password_wrong_current_rejected(self, client):
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        access_token = r.json()["tokens"]["access_token"]
        r = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "totally-wrong", "new_password": "newpassword123"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert r.status_code == 400

    async def test_change_password_success_revokes_sessions(self, client):
        await _register_and_verify(client)
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
        )
        tokens = r.json()["tokens"]
        access_token = tokens["access_token"]

        r = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": REGISTER_PAYLOAD["password"],
                "new_password": "brandnewpassword123",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert r.status_code == 204

        # Old refresh token should now be revoked.
        r = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert r.status_code == 401

        # Login with the new password should work.
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": "brandnewpassword123"},
        )
        assert r.status_code == 200
