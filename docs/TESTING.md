# Testing Strategy

## The one rule that matters most

**Never run `pytest` against the shared docker-compose dev stack's database.**

The backend test suite truncates every table and flushes Redis before
*every single test* (`tests/conftest.py`'s autouse `_reset_database` and
`_flush_cache` fixtures). That's correct and safe against a disposable,
test-only database. It is destructive against any database a human or a
running service actually depends on.

On 2026-08-05 this happened for real: `docker compose exec backend pytest`
was run to sanity-check a fix, and it silently wiped the dev stack's seeded
catalog data (8 datasets, 785 records) and an in-progress manual
upload-pipeline verification, because that container's `DATABASE_URL` and
`REDIS_URL` point at the same Postgres/Redis the dev stack — and anyone
using it — depends on. A guard now exists specifically to stop this from
happening again silently (see below), but the underlying rule doesn't
change: **pick the right target before you run the suite.**

## The two databases in this project

| | Purpose | How to reach it | Data lifetime |
|---|---|---|---|
| **Dev stack DB** (`docker-compose.yml` → `postgres`/`redis` services) | Backs the running application — `backend`, `celery-worker`, `celery-beat`, `frontend` containers, and anyone browsing `localhost:3000`/`localhost:8000` | `docker compose up`; internally reachable at the Docker service hostnames `postgres`/`redis` | Long-lived. Seeded via `python -m app.scripts.seed_catalog`, or accumulates real data from manual testing/uploads. **Never truncated by test code.** |
| **Isolated dev/test DB** | Backs `pytest` only | Bare metal: `backend/.env` → `localhost:55434` (Postgres) / `localhost:56380` (Redis), started separately from the main compose stack. Container: override `DATABASE_URL`/`REDIS_URL` to point at those same isolated containers (see below) | Disposable. Truncated before every test, on purpose. |

These are two different Postgres instances and two different Redis
instances, on different ports, with no data in common. That separation is
the entire safety mechanism — the test suite is *allowed* to be destructive
precisely because it's destructive only to a database nothing else touches.

## Running the backend suite

**Bare metal (normal case):**

```bash
cd backend
pytest
```

This picks up `backend/.env`, which points at the isolated dev containers
on `localhost:55434`/`localhost:56380`. Make sure those containers are
running first (they are separate from `docker compose up` — check with
`docker ps --filter name=bodp_dev`).

**Inside a container**, when you need the exact same Python/OS environment
as the deployed backend:

```bash
docker compose run --rm \
  -e DATABASE_URL="postgresql+asyncpg://bodp:bodp@host.docker.internal:55434/bodp" \
  -e DATABASE_URL_SYNC="postgresql+psycopg://bodp:bodp@host.docker.internal:55434/bodp" \
  -e REDIS_URL="redis://host.docker.internal:56380/0" \
  -e CELERY_BROKER_URL="redis://host.docker.internal:56380/1" \
  -e CELERY_RESULT_BACKEND="redis://host.docker.internal:56380/2" \
  -e STORAGE_VPS_PUBLIC_ENDPOINT_URL="http://minio:9000" \
  backend pytest
```

The `STORAGE_VPS_PUBLIC_ENDPOINT_URL` override matters for
`tests/test_storage.py`'s presigned-URL tests specifically: presigned URLs
are meant to be followed from *outside* the Docker network (a real
browser), so they're built against the compose stack's published host port
(`localhost:9000`) by default. But a `docker compose run` container is
*inside* that network, where `localhost:9000` doesn't reach the `minio`
service at all — pointing the override back at the internal service name
makes the presign tests resolve correctly for a test process that happens
to run inside the network they're designed to be followed from outside of.
Bare-metal pytest needs no such override (`backend/.env`'s
`STORAGE_VPS_ENDPOINT_URL=http://localhost:9000` already IS reachable from
a bare-metal process, so the internal/public split collapses to the same
value there).

**Never** `docker compose exec backend pytest` (or `run` without the
overrides above) — both inherit `docker-compose.yml`'s `DATABASE_URL`,
which resolves to the live dev stack.

## The fail-fast guard

`backend/tests/conftest.py` checks the resolved `DATABASE_URL`/`REDIS_URL`
hostnames at import time, before any fixture (including the truncating
ones) can run:

- If the Postgres host is literally `postgres`, or the Redis host is
  literally `redis` — the exact hostnames `docker-compose.yml`'s
  `x-backend-env` anchor assigns to the `backend`/`celery-worker`/
  `celery-beat` services — the suite prints an explanation and exits
  immediately (`SystemExit(1)`) without collecting or running a single
  test.
- No legitimate pytest target (bare metal against `backend/.env`, the
  isolated dev containers, or a real CI database) ever resolves to those
  hostnames, so this check has no legitimate false positives — if it
  fires, the command really was about to hit the shared dev stack.

This is a backstop, not a substitute for running the right command. It
catches the exact mistake made on 2026-08-05; it will not catch every way
of misconfiguring `DATABASE_URL` to point somewhere else important (e.g. a
staging server's connection string pasted into a local `.env` by accident).
Point pytest at disposable databases only.

## Frontend

`frontend/` has no backend/database dependency of its own for its current
test tooling (`npx tsc --noEmit`, `npm run build`, `npm run lint`) — those
are static checks and don't touch any database. If integration tests that
exercise the live API are added later, they must follow the same rule:
target the isolated dev backend, never the dev stack's `backend` container
directly in a way that could mutate its data.

## Verifying real ingestion, not just the catalog read path

`app/scripts/seed_catalog.py` inserts `Dataset`/`DatasetRecord`/`Station`
rows directly via the ORM — it does **not** exercise the upload → Celery
ingestion → parser → metadata-extraction pipeline (`POST
/admin/datasets/{id}/files` → `app/worker/tasks/ingestion.py`). It exists
to give the catalog UI/API realistic browsable data without manually
uploading files by hand on every reset.

Pytest's own `tests/test_dataset_upload.py` covers the real pipeline in
isolation (eager Celery, in-process test client) and is sufficient for
day-to-day development. But because it runs Celery in eager/synchronous
mode, it does not prove the pipeline works over a real broker with a
separate worker process. Before signing off on any change that touches
upload, storage, or ingestion code, do at least one manual round-trip
through the live dev stack:

1. Register + verify a user, grant it the `Edit Datasets` permission (see
   `register_verified_user` in `tests/conftest.py` for the exact DB calls —
   run them by hand against the dev stack's database, not through pytest).
2. `POST /admin/datasets` to create a dataset shell, then `POST
   /admin/datasets/{id}/files` with a real file, against `localhost:8000`
   (the dev stack, not the isolated test DB).
3. Poll `GET /admin/datasets/uploads/{upload_id}` until `status: complete`,
   and confirm in `docker compose logs celery-worker` that the **separate
   worker container** picked up the task from Redis — this is what proves
   the real broker path works, not just the ingestion logic itself.
4. Spot-check the resulting `Dataset` row's extracted metadata
   (`record_count`, `parameters`, `formats`, `temporal_start/end`,
   `spatial_extent`) against what the source file actually contains.
5. Clean up the fixture dataset/user afterward — this workflow writes to
   the dev stack's real database, so don't leave test rows behind.
