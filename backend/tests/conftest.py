import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.limiter import limiter
from app.core.security import create_email_verification_token
from app.main import app
from app.models.user import Role, User, UserRole
from app.worker.celery_app import celery_app

# Rate limiting is a real, deliberate production behavior (Master Plan Phase 1
# task 6) — but slowapi's default key_func treats every request from the test
# client as the same IP, so the suite would trip the auth rate limit almost
# immediately. Disable enforcement for the test session; rate limiting itself
# is covered separately if/when a dedicated rate-limit test is added.
limiter.enabled = False

# Run Celery tasks synchronously, in-process, instead of dispatching through
# the Redis broker to a separate worker process — the test suite doesn't run
# a worker, and eager mode lets .delay() calls (e.g. dataset file ingestion)
# execute and complete before the HTTP response returns, so tests can assert
# on post-ingestion state immediately without polling/sleeping.
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


@pytest_asyncio.fixture(autouse=True)
async def _reset_database():
    """Truncate all tables between tests so each test starts from a clean slate,
    without paying the cost of re-running migrations per test."""
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def register_verified_user(
    client: AsyncClient, *, email: str, admin: bool = False, permissions: list[str] | None = None
) -> str:
    """Registers + verifies + (optionally) elevates a user to admin with a
    given set of fine-grained permissions, then logs in. Returns an access
    token. Shared across test modules that need an authenticated admin."""
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Fixture User",
            "email": email,
            "password": "testpass123",
            "institution": None,
            "phone": None,
        },
    )
    assert r.status_code == 201, r.text

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        token = create_email_verification_token(str(user.id), user.email)
        if admin:
            user.role = "admin"
        await db.commit()

        if permissions:
            role = Role(name=f"role-for-{email}", description="test role", permissions=permissions)
            db.add(role)
            await db.flush()
            db.add(UserRole(user_id=user.id, role_id=role.id))
            await db.commit()

    r = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200, r.text

    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "testpass123"})
    assert r.status_code == 200, r.text
    return r.json()["tokens"]["access_token"]


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient) -> dict:
    token = await register_verified_user(
        client,
        email="phase2-admin@example.com",
        admin=True,
        permissions=["Edit Datasets", "Delete Datasets"],
    )
    return {"Authorization": f"Bearer {token}"}
