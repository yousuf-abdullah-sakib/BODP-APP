import sys

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.config import settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.limiter import limiter
from app.core.security import create_email_verification_token
from app.main import app
from app.models.user import Role, User, UserRole
from app.worker.celery_app import celery_app

# --- Guard: refuse to run against the shared docker-compose dev stack ---
#
# This suite truncates every table before EVERY test (see _reset_database
# below) and flushes Redis before every test (see _flush_cache). That is
# correct and safe against a disposable, test-only database — but on
# 2026-08-05 running `docker compose exec backend pytest` wiped the shared
# dev stack's seeded catalog data and in-progress manual test fixtures,
# because that container's DATABASE_URL/REDIS_URL point at the same Postgres
# and Redis the dev stack (and any human using it) depends on.
#
# docker-compose.yml's `backend`/`celery-worker`/`celery-beat` services are
# the only place DATABASE_URL resolves to the literal Docker Compose service
# hostname "postgres" (see docker-compose.yml's x-backend-env anchor) — no
# legitimate pytest target (bare-metal against backend/.env, the isolated
# dev containers on localhost:55434, or CI) ever resolves to that hostname.
# So this hostname check is a precise, non-heuristic way to detect "pytest
# is about to run inside the shared dev stack" and abort before any fixture
# — including this file's own autouse ones — gets a chance to touch data.
# See docs/TESTING.md for the full policy this enforces.
_FORBIDDEN_DB_HOSTS = {"postgres"}
_FORBIDDEN_REDIS_HOSTS = {"redis"}


def _abort_if_dev_stack_database() -> None:
    # PostgresDsn is a MultiHostUrl in pydantic v2 (supports replica-set
    # style DSNs) — `.host` isn't available, only `.hosts()`, a list.
    # RedisDsn is a plain single-host Url and exposes `.host` directly.
    db_hosts = settings.DATABASE_URL.hosts()
    db_host = db_hosts[0]["host"] if db_hosts else None
    redis_host = settings.REDIS_URL.host
    if db_host in _FORBIDDEN_DB_HOSTS or redis_host in _FORBIDDEN_REDIS_HOSTS:
        sys.stderr.write(
            "\n"
            "REFUSING TO RUN: pytest is configured against the shared docker-compose\n"
            f"dev stack (DATABASE_URL host={db_host!r}, REDIS_URL host={redis_host!r}).\n"
            "This test suite truncates every table and flushes Redis before every\n"
            "test — running it here would wipe real seeded/manually-created data.\n"
            "\n"
            "Run pytest against the isolated dev database instead:\n"
            "  - Bare metal: `cd backend && pytest` (uses backend/.env, localhost:55434)\n"
            "  - Container:  `docker compose run --rm backend pytest` with\n"
            "    DATABASE_URL/REDIS_URL overridden to the isolated dev containers —\n"
            "    never `docker compose exec backend pytest`.\n"
            "See docs/TESTING.md for the full policy.\n"
        )
        raise SystemExit(1)


_abort_if_dev_stack_database()

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


@pytest_asyncio.fixture(autouse=True)
async def _flush_cache():
    """Flushes the Redis cache before each test — catalog taxonomy (and any
    future cached endpoint) must not leak state across tests that each seed
    their own fresh data (Master Plan §3 Phase 3 task 6 caching)."""
    await get_redis().flushdb()
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
