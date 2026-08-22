# BODP — Bangladesh Ocean/Environmental Data Platform

A full-stack platform for cataloging, requesting, and visualizing
Bangladesh's ocean and environmental datasets — dataset discovery and
search, a filtered-request-and-grant access workflow, subset extraction,
and scientific visualization (time series, spatial interpolation,
comparison, statistics, depth profiles) over tabular, gridded (NetCDF/
Zarr), and raster (GeoTIFF) data.

Backend: FastAPI (Python) + PostgreSQL/PostGIS + Celery/Redis + S3-compatible
object storage. Frontend: Next.js/React. See `MASTER_PLAN.md` for the full
architecture, phase history, and the decisions behind every major choice.

## Documentation map

- **`MASTER_PLAN.md`** / **`PLAN.md`** — architecture, phased build
  history, and the reasoning behind every major decision. Start here for
  "why does this work this way."
- **`docs/VPS_PROVISIONING.md`** — deployment runbook: provisioning,
  hardening, TLS, bringing up the production stack, secrets rotation.
- **`docs/ENVIRONMENT_VARIABLES.md`** — every backend configuration
  setting, its default, and what changing it actually affects.
- **`docs/DISASTER_RECOVERY.md`** — concrete recovery procedures for
  database loss, object storage loss, and full VPS loss; states plainly
  what is and isn't recoverable today.
- **`docs/TESTING.md`** — how to run the backend test suite correctly
  (which database/Redis instance to point at, and the one command never
  to run against the live dev stack).
- **`docs/SECURITY_AUDIT.md`** — OWASP-category security review
  findings, rate-limiting policy, and the security-relevant changelog.
- **`docs/DATA_INTEGRITY.md`** — checksum-on-read verification and the
  orphan-sweep (DB/storage drift detection) mechanism.
- **`docs/LOAD_TESTING.md`** — the load-testing tool and captured
  real-traffic benchmark results.
- **`docs/FRONTEND_CROSSCHECK.md`** — final page-by-page verification
  that every interactive element across the site calls a real backend
  endpoint.

## Quick start (local development)

```bash
docker compose up
```

Brings up Postgres/PostGIS, Redis, MinIO, the FastAPI backend, Celery
workers, Celery beat, and the Next.js frontend. Backend at
`http://localhost:8000`, frontend at `http://localhost:3000`.

Seed sample catalog data:

```bash
docker compose exec backend python -m app.scripts.seed_catalog
```

Run the backend test suite — see `docs/TESTING.md` first; it documents
exactly which Postgres/Redis instance the tests must point at and the one
command that must never be run against the live dev stack (it will
truncate real data).

## Deployment

See `docs/VPS_PROVISIONING.md` for the full runbook. In short:
`docker compose -f docker-compose.prod.yml up -d --build` on a
provisioned VPS with `.env.prod` configured from `.env.prod.example`.
