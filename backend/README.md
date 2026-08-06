# BODP Backend

FastAPI backend for the Bangladesh Ocean/Environmental Data Platform. See
`../MASTER_PLAN.md` at the repo root for the full system design and phase
breakdown — this README only covers running this service locally.

## Local development (without Docker)

```bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate on Linux/Mac
pip install -e ".[dev]"
cp .env.example .env            # edit as needed
```

You need a running Postgres+PostGIS and Redis. Easiest path is the repo-root
`docker-compose.yml` (see below) — point `.env` at whatever ports it exposes.

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

## Local development (Docker, full stack)

From the repo root:

```bash
docker compose up -d --build
```

This starts Postgres+PostGIS, Redis, MinIO, the backend (with autoreload),
and Celery worker/beat, all under the `bodp-platform` compose project name
(chosen deliberately to avoid colliding with any other project on the same
machine). Backend is reachable at `http://localhost:8000`, MinIO console at
`http://localhost:9001`, Postgres at `localhost:55432` (non-default port,
same reasoning).

## Running tests

**Never run pytest against the `docker-compose.yml` dev stack's database —
see [`../docs/TESTING.md`](../docs/TESTING.md) for the full policy and why
it matters.** Tests run against a real Postgres+PostGIS, not mocks, and the
suite truncates every table before every test — safe against a disposable
instance, destructive against the dev stack's real data. A guard in
`tests/conftest.py` refuses to run (`SystemExit(1)`) if it detects
`DATABASE_URL`/`REDIS_URL` pointing at the dev stack's `postgres`/`redis`
service hostnames, but don't rely on the guard — point pytest at a
disposable instance in the first place:

```bash
docker run -d --name bodp_pytest_postgres -e POSTGRES_USER=bodp \
  -e POSTGRES_PASSWORD=bodp -e POSTGRES_DB=bodp -p 55434:5432 postgis/postgis:17-3.5
docker run -d --name bodp_pytest_redis -p 56380:6379 redis:7-alpine
```

Point `.env`'s `DATABASE_URL`/`DATABASE_URL_SYNC` at port 55434 and
`REDIS_URL`/`CELERY_*` at port 56380, then:

```bash
alembic upgrade head
pytest tests/ -v
```

The test suite truncates all tables between tests (see `tests/conftest.py`)
rather than re-running migrations per test, and disables rate limiting
(`slowapi`'s default IP-based key function treats every test-client request
as the same origin, which would otherwise trip the real auth rate limit
almost immediately).

## Project layout

```
app/
  core/       config, database session, security (JWT/bcrypt), RBAC
              permission dependency, logging, error handlers, rate limiter
  models/     SQLAlchemy models — one module per domain area
  schemas/    Pydantic request/response schemas
  routers/    FastAPI route handlers (thin — delegate to services/)
  services/   business logic (auth_service, email_service, ...)
  worker/     Celery app + tasks (populated from Phase 2 onward)
alembic/      migrations
tests/        pytest suite
```
