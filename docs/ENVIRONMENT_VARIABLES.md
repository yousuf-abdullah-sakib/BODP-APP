# Environment Variables

Every variable the backend reads, sourced from `backend/app/core/config.py`
(the single place configuration is defined — nothing reads `os.environ`
directly anywhere else). `.env.prod.example` (repo root) is the minimal
"must set on the VPS" list; this document extends it with every setting
that has a non-obvious default or an operational consequence worth
understanding before changing it.

Trivial pass-through settings (app name, token expiry windows that rarely
need tuning) are omitted here where their name and default already say
everything relevant — only settings with a real "why this number" or "what
breaks if you change it" story get an entry.

## App

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` / `staging` / `production`. Gates `is_production` — disables `/api/docs`/`/api/redoc`/OpenAPI schema exposure in production (`main.py`). |
| `DEBUG` | `false` | Must be `false` in production — leave unset there rather than explicitly setting `true`. |
| `FRONTEND_URL` / `BACKEND_URL` | `localhost` values | Used for building links in transactional emails and generating absolute URLs. Set to the real public domain in production. |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Must be the exact production frontend origin(s) in production — never a wildcard. |

`APP_NAME` and `API_V1_PREFIX` are intentionally omitted above — display/
routing constants with no real operational consequence to document.

## Database

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `localhost:5432` | Async (asyncpg) connection string — the app's primary DB access path. |
| `DATABASE_URL_SYNC` | `localhost:5432` | Sync (psycopg) connection string — used by Alembic migrations and every Celery worker task (Celery tasks don't run inside an event loop, so they use a sync session rather than a second async engine per task). |
| `DB_POOL_SIZE` | `10` | Per-process connection pool size. Sized against Postgres's own `max_connections` (100 on the `postgis/postgis:17-3.5` image) divided across every process that holds its own independent pool against the same database: 4 Gunicorn API workers + `celery-worker` + `celery-worker-ingestion` = 6 processes. `(DB_POOL_SIZE + DB_MAX_OVERFLOW) × 6` must stay comfortably under 100 — the current 15×6=90 leaves headroom for Postgres's reserved/superuser connections and any ad-hoc `psql` session. **Raising this without also raising Postgres's `max_connections` (or reducing worker count) risks `TooManyConnectionsError` under real concurrent load** — confirmed as a real failure mode during this project's own load testing, not a theoretical concern. |
| `DB_MAX_OVERFLOW` | `5` | See `DB_POOL_SIZE` above — same sizing math applies to the combined total. |

## Redis / Celery

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `localhost:6379/0` | App-level cache (catalog search results, CMS blocks, etc.) — Redis DB index 0. |
| `CELERY_BROKER_URL` | `localhost:6379/1` | Celery task queue — Redis DB index 1, kept separate from the cache so a cache flush never touches queued/in-flight jobs. |
| `CELERY_RESULT_BACKEND` | `localhost:6379/2` | Celery task results — Redis DB index 2. |

Two queues exist: the default queue (`celery-worker` service) and a
dedicated `ingestion` queue (`celery-worker-ingestion`, concurrency=1) for
dataset-file ingestion and bulk-import transfers — large, memory-heavy
jobs that must never queue behind or block lightweight tasks like
notifications.

## Auth / JWT

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET_KEY` | insecure dev placeholder | **Must be overridden with a strong random value (32+ chars) in every non-development environment.** Rotating this invalidates every active session immediately — see the secrets-rotation section of `docs/VPS_PROVISIONING.md`. |
| `JWT_ALGORITHM` | `HS256` | Not expected to change. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | How long a bearer token is valid before the client must refresh. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `14` | How long a user stays logged in without re-entering credentials. |

`EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS` (48), `PASSWORD_RESET_TOKEN_EXPIRE_HOURS`
(2), and `INVITE_TOKEN_EXPIRE_HOURS` (72) follow the same pattern — link
validity windows for their respective one-time-token flows. Defaults are
reasonable for most deployments and rarely need tuning; change only if a
specific usability or security requirement calls for a shorter/longer
window.

## Security

| Variable | Default | Purpose |
|---|---|---|
| `FAILED_LOGIN_LOCKOUT_THRESHOLD` | `5` | Failed login attempts (same email) before a temporary lockout. |
| `FAILED_LOGIN_LOCKOUT_WINDOW_MINUTES` | `15` | How long the lockout lasts, and the window over which failed attempts are counted. |
| `BCRYPT_ROUNDS` | `12` | Password hashing cost factor — higher is slower to brute-force but slower to hash on every login; 12 is a standard, well-tested value. |

## Rate limiting

| Variable | Default | Purpose |
|---|---|---|
| `RATE_LIMIT_AUTH` | `10/minute` | Login/register/refresh — the tightest limit, since these are the highest-value target for a scripted attack. |
| `RATE_LIMIT_DEFAULT` | `120/minute` | The general floor applied to most routes — read-only browsing (catalog search, dataset detail) is deliberately unrestricted beyond this. |
| `RATE_LIMIT_MUTATIONS` | `30/minute` | Stricter limit for routes that write real DB rows and/or dispatch a Celery job: data request submission, extraction creation, and the compute-heavy Visualize analysis endpoints (timeseries/spatial/comparison/statistics/profiles). Confirmed via real load testing (`docs/LOAD_TESTING.md`) to hold exactly at its configured ceiling under 500 concurrent requests with zero server errors. |

Format is slowapi/`limits` syntax (`"<count>/<period>"`). A known,
documented gap exists where roughly 20 admin router files aren't
individually rate-limited due to a FastAPI/slowapi routing
incompatibility — see `docs/SECURITY_AUDIT.md` for the full writeup and
tracked scope.

## Storage (S3-compatible)

| Variable | Default | Purpose |
|---|---|---|
| `STORAGE_VPS_ENDPOINT_URL` | `localhost:9000` | Internal endpoint the backend/worker processes use to reach the primary (VPS-local MinIO) storage tier. |
| `STORAGE_VPS_PUBLIC_ENDPOINT_URL` | unset (falls back to the internal endpoint) | The endpoint presigned URLs are built against — must be reachable from *outside* the Docker network (a real browser). Only needs to differ from `STORAGE_VPS_ENDPOINT_URL` in a Docker Compose setup where the internal service name (`minio`) isn't publicly routable; in production this is the VPS's public domain. |
| `STORAGE_VPS_ACCESS_KEY` / `STORAGE_VPS_SECRET_KEY` | dev placeholders | MinIO/S3 credentials for the primary storage tier. Rotating requires a coordinated update + service restart — see `docs/VPS_PROVISIONING.md`. |
| `STORAGE_VPS_BUCKET` | `bodp-vps` | Bucket name for the primary storage tier. |
| `STORAGE_CLOUD_*` | all unset | Optional second storage tier (cloud object storage — provider undecided, see Master Plan §5). All four of `STORAGE_CLOUD_ENDPOINT_URL`/`_ACCESS_KEY`/`_SECRET_KEY` must be set together for the "cloud" backend to be considered configured anywhere in the app (health checks, orphan sweep, etc.) — partial configuration is treated as unconfigured, not a partial/degraded state. |
| `STORAGE_MAX_POOL_CONNECTIONS` | `32` | boto3 HTTP connection pool size. Benchmarked directly against a real multi-thousand-object Zarr store upload — raising this from boto3's own default (10) measurably increased throughput; sized as roughly 2× `STORAGE_UPLOAD_CONCURRENCY` so every upload thread can hold its own connection with headroom. |
| `STORAGE_MAX_RETRIES` | `3` | boto3's built-in retry count for transient storage errors (connection reset, timeout, 5xx) — covers brief MinIO unavailability without any custom retry loop. |
| `STORAGE_UPLOAD_CONCURRENCY` | `16` | Bounded concurrent-upload thread count for multi-object (Zarr) uploads — benchmarked plateau point; not unbounded, so a store with tens of thousands of chunks can't spawn tens of thousands of threads. |

## Ingestion timeouts

| Variable | Default | Purpose |
|---|---|---|
| `MAX_UPLOAD_SIZE_MB` | `5000` | Hard cap on a single uploaded dataset file. |
| `MAX_SUPPORTING_DOCUMENT_SIZE_MB` | `3` | Cap on a Data Request's supporting-document attachment (PDF/DOC/DOCX) — deliberately far below `MAX_UPLOAD_SIZE_MB`, since this is a small justification attachment, not a scientific dataset file. |
| `LEGACY_MAT_MAX_SIZE_MB` | `2000` | Rejects legacy (pre-v7.3) `.mat` files above this size with a clear message pointing at the real fix (re-save as v7.3 in MATLAB). Legacy `.mat` files load entirely into memory with no chunked-read API; v7.3 files are HDF5-based and can be read in slices, so this limit only affects the older format. |
| `DEFAULT_PROJECTED_CRS` | `EPSG:32646` (UTM zone 46N) | Fallback coordinate reference system for a gridded `.mat` file whose coordinates are clearly projected (not lon/lat) but carry no embedded projection metadata. UTM 46N covers Bangladesh's coastal operating area; only change if ingesting gridded `.mat` data from a different region with the same metadata gap. |
| `INGESTION_SOFT_TIME_LIMIT_BASE_SECONDS` | `300` | Baseline time allowance before the per-file scaling below is added — so a trivial file isn't held to an unreasonably long deadline. |
| `INGESTION_SOFT_TIME_LIMIT_SECONDS_PER_GB` | `180` | Additional seconds allowed per GB of file size, on a deliberately conservative ~50MB/s effective-throughput assumption covering download + parse + convert + upload combined. **This is the single knob most worth tuning** once a deployment's real measured ingestion throughput (actual VPS disk/network/CPU) is known — raise it if large-file ingestion is timing out in practice. |
| `INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS` | unset (unlimited) | Optional hard ceiling on the computed soft limit, regardless of file size. Leave unset unless a deployment wants to cap even a TB-scale file's allowed processing time. |
| `INGESTION_HARD_TIME_LIMIT_GRACE_SECONDS` | `600` | Worker-level SIGKILL failsafe margin above the soft limit — a backstop for the pathological case where a task doesn't respond to the soft timeout signal at all. Should never fire under normal operation. |
| `INGESTION_ZARR_TIME_CHUNK_SIZE` | `200` | Zarr write chunk size along the time axis for gridded data. Benchmarked directly against a real ~2GB, 43,825-timestep NetCDF file — 200 produced far fewer, larger chunk objects than the previous default (24) and was strictly faster on every query pattern tested. Re-benchmark before raising further; a large enough chunk eventually starts over-reading for narrow time-range queries. |

## Subset extraction

| Variable | Default | Purpose |
|---|---|---|
| `EXTRACTION_DOWNLOAD_URL_EXPIRE_MINUTES` | `60` | How long a presigned extraction-download URL stays valid. |
| `EXTRACTION_SYNC_THRESHOLD_MB` | `10` | Extractions at or below this size complete synchronously (the requesting HTTP call waits); larger ones dispatch to Celery and the client polls for completion. |

## Backups

| Variable | Default | Purpose |
|---|---|---|
| `BACKUP_RETENTION_DAYS` | `30` | How long a nightly `pg_dump` backup (and its storage object) is kept before the retention sweep deletes it. |
| `BACKUP_DOWNLOAD_URL_EXPIRE_MINUTES` | `10` | Deliberately much shorter than `EXTRACTION_DOWNLOAD_URL_EXPIRE_MINUTES` — a backup dump is full production data, a strictly more sensitive artifact than any single scoped extraction. |
| `BACKUP_PG_DUMP_TIMEOUT_SECONDS` | `3600` | Hard ceiling on the `pg_dump` subprocess — a hung dump must not occupy a Celery worker slot indefinitely. Raise if a real production database genuinely needs longer than an hour to dump. |

## Monitoring

| Variable | Default | Purpose |
|---|---|---|
| `DISK_USAGE_CHECK_PATH` | `/` | Filesystem path the admin health dashboard's disk-usage check reads. Best-effort inside a container — only reflects the real host disk usage if this path is backed by a bind-mounted volume; VPS-level disk monitoring is the authoritative source in production. |

## Visualization engine

| Variable | Default | Purpose |
|---|---|---|
| `VIZ_SPATIAL_SYNC_THRESHOLD_CELLS` | `40,000` | Spatial interpolation requests at or below this work-unit count (point count × resolution²) compute synchronously; larger ones dispatch to Celery. |
| `VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES` | `200,000,000` | Statistics/Comparison requests touching this much combined Parquet/Zarr file data dispatch to Celery instead of computing in-process. Legacy (pre-Phase-5) datasets with no Parquet/Zarr files always compute synchronously regardless. |
| `QUERY_CONCURRENCY_LIMIT_PER_WORKER` | `2` | Caps concurrent DuckDB/xarray queries per Gunicorn worker process — real load-tested and re-benchmarked twice (see `docs/LOAD_TESTING.md` and the setting's own docstring in `config.py` for the full A/B numbers). **Do not change this without re-running the load test** — both higher and lower values were measured to perform worse or equal at every concurrency level tested. A request that can't acquire a slot queues on the semaphore itself, still holding its Postgres connection — this is real, expected, documented queueing behavior under heavy concurrent visualization load, not a bug. |
| `QUERY_DUCKDB_THREADS_PER_CONNECTION` | `2` | DuckDB's own internal per-connection thread count — a different lever from the setting above (this bounds threads per query, not how many queries run concurrently). Re-benchmarked at 1 vs 2 with no measurable CPU difference; left at 2. |

## Email

| Variable | Default | Purpose |
|---|---|---|
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` | dev placeholders | Any SMTP-compatible transactional provider works (Brevo, Postmark, SES, Resend, etc.) — see `.env.prod.example` for a free-tier Brevo pointer. Rotating credentials is hot-reloadable, no downtime. |
| `SMTP_USE_TLS` | `true` | Leave enabled unless the provider explicitly requires otherwise. |
| `EMAIL_FROM_ADDRESS` / `EMAIL_FROM_NAME` | `no-reply@bodp.example.org` / `BODP Platform` | Sender identity on every transactional email. |
