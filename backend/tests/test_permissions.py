import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.permissions import require_admin, require_permission
from app.core.security import create_email_verification_token
from app.main import app
from app.models.user import Role, User, UserRole

pytestmark = pytest.mark.asyncio

# A throwaway router exercising require_permission, mounted only for this test
# module, so the permission dependency is tested against real request/response
# plumbing (JWT parsing, DB lookups) rather than called as a bare function.
_test_router = APIRouter(prefix="/_test-only")


@_test_router.get("/admin-only")
async def _admin_only(user: User = Depends(require_admin)):
    return {"ok": True}


@_test_router.get("/approve-requests")
async def _requires_approve_requests(
    user: User = Depends(require_permission("Approve Requests")),
):
    return {"ok": True}


app.include_router(_test_router)


async def _create_user(client, *, email: str, admin: bool = False) -> str:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Perm Test",
            "email": email,
            "password": "testpass123",
            "institution": None,
            "phone": None,
        },
    )
    assert r.status_code == 201

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        token = create_email_verification_token(str(user.id), user.email)
        if admin:
            user.role = "admin"
        await db.commit()

    r = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200

    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "testpass123"})
    assert r.status_code == 200
    return r.json()["tokens"]["access_token"]


async def _grant_role_with_permissions(email: str, permissions: list[str]) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one()

        role = Role(name=f"role-for-{email}", description="test role", permissions=permissions)
        db.add(role)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()


class TestCoarseAdminGate:
    async def test_regular_user_denied_admin_only_endpoint(self, client):
        token = await _create_user(client, email="regular@example.com", admin=False)
        r = await client.get(
            "/_test-only/admin-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    async def test_admin_user_allowed_admin_only_endpoint(self, client):
        token = await _create_user(client, email="admin1@example.com", admin=True)
        r = await client.get(
            "/_test-only/admin-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200


class TestFineGrainedPermission:
    async def test_admin_without_permission_denied(self, client):
        """An admin who holds no Role granting 'Approve Requests' must be
        rejected — coarse admin role alone is not sufficient (Master Plan §0:
        'make permissions real')."""
        token = await _create_user(client, email="admin2@example.com", admin=True)
        r = await client.get(
            "/_test-only/approve-requests", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    async def test_admin_with_permission_allowed(self, client):
        email = "admin3@example.com"
        token = await _create_user(client, email=email, admin=True)
        await _grant_role_with_permissions(email, ["Approve Requests", "Manage Users"])

        r = await client.get(
            "/_test-only/approve-requests", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200

    async def test_admin_with_unrelated_permission_still_denied(self, client):
        email = "admin4@example.com"
        token = await _create_user(client, email=email, admin=True)
        await _grant_role_with_permissions(email, ["Manage Users"])  # not "Approve Requests"

        r = await client.get(
            "/_test-only/approve-requests", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    async def test_regular_user_with_role_permission_still_denied(self, client):
        """Fine-grained permissions only ever apply within the admin surface —
        a base 'user' role must be rejected regardless of any Role grant."""
        email = "notadmin@example.com"
        token = await _create_user(client, email=email, admin=False)
        await _grant_role_with_permissions(email, ["Approve Requests"])

        r = await client.get(
            "/_test-only/approve-requests", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403
