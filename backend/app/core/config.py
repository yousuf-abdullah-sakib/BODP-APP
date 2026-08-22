from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application configuration, sourced from environment variables.

    Every later phase reads config through this module rather than
    `os.environ` directly, so required vars are documented in one place.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    APP_NAME: str = "BODP API"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    FRONTEND_URL: str = "http://localhost:3000"
    BACKEND_URL: str = "http://localhost:8000"

    # --- Database ---
    DATABASE_URL: PostgresDsn = Field(
        default="postgresql+asyncpg://bodp:bodp@localhost:5432/bodp"
    )
    DATABASE_URL_SYNC: str = Field(
        default="postgresql+psycopg://bodp:bodp@localhost:5432/bodp",
        description="Sync driver URL, used by Alembic and Celery workers.",
    )
    # Sized against Postgres's own max_connections (100 by default on
    # this project's postgres:17-3.5 image — confirmed via `SHOW
    # max_connections` during PLAN.md Phase 5's production-mode
    # concurrency benchmark), NOT chosen independently per process. Six
    # separate processes each hold their own pool against the same
    # Postgres instance: 4 Gunicorn API workers (production Dockerfile,
    # --workers 4) + celery-worker (concurrency=2, still one pool per
    # process) + celery-worker-ingestion (concurrency=1). At the
    # previous default (pool_size=20, max_overflow=10 = 30/process), 6
    # processes could demand up to 180 connections against a 100-
    # connection server — genuinely oversubscribed by design, which the
    # benchmark surfaced as asyncpg.exceptions.TooManyConnectionsError
    # ("sorry, too many clients already") under real concurrent load,
    # not merely a slow-query symptom. 15/process x 6 processes = 90,
    # leaving headroom under 100 for Postgres's own reserved/superuser
    # connections and any ad-hoc psql/admin session.
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5

    # --- Redis / Celery ---
    REDIS_URL: RedisDsn = Field(default="redis://localhost:6379/0")
    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/1")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/2")

    # --- Auth / JWT ---
    JWT_SECRET_KEY: str = Field(
        default="CHANGE_ME_INSECURE_DEV_ONLY_SECRET_KEY_MIN_32_CHARS",
        description="Must be overridden via env var in staging/production.",
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 48
    PASSWORD_RESET_TOKEN_EXPIRE_HOURS: int = 2
    INVITE_TOKEN_EXPIRE_HOURS: int = 72

    # --- Security ---
    FAILED_LOGIN_LOCKOUT_THRESHOLD: int = 5
    FAILED_LOGIN_LOCKOUT_WINDOW_MINUTES: int = 15
    BCRYPT_ROUNDS: int = 12
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Rate limiting ---
    RATE_LIMIT_AUTH: str = "10/minute"
    RATE_LIMIT_DEFAULT: str = "120/minute"
    # Phase 10.2 (hardening plan): stricter than RATE_LIMIT_DEFAULT for
    # routes that write real DB rows AND dispatch a real Celery job —
    # request submission/extraction creation, and the
    # QUERY_CONCURRENCY_LIMIT_PER_WORKER-gated Visualize endpoints. A
    # scripted abuse loop against these could flood the request-approval
    # queue, spawn excess extraction jobs, or exhaust the semaphore pool
    # faster than legitimate traffic would — catalog/browsing endpoints
    # deliberately stay on RATE_LIMIT_DEFAULT since read-only browsing at
    # 120/min is correctly unrestricted further.
    RATE_LIMIT_MUTATIONS: str = "30/minute"

    # --- Storage (S3-compatible; provider-agnostic per Master Plan) ---
    STORAGE_VPS_ENDPOINT_URL: str = "http://localhost:9000"
    # Endpoint presigned URLs are built against — must be reachable from
    # outside the Docker network (a browser, curl), unlike
    # STORAGE_VPS_ENDPOINT_URL which the backend/worker use internally.
    # Defaults to the internal endpoint so single-endpoint setups (real
    # cloud S3/R2/B2 in production, or non-Docker local dev) need no extra
    # config; only diverges in a Docker Compose dev/staging setup like this
    # one's, where MinIO's internal service name isn't publicly routable.
    STORAGE_VPS_PUBLIC_ENDPOINT_URL: str | None = None
    STORAGE_VPS_ACCESS_KEY: str = "bodp_minio_admin"
    STORAGE_VPS_SECRET_KEY: str = "CHANGE_ME_MINIO_SECRET"
    STORAGE_VPS_BUCKET: str = "bodp-vps"
    STORAGE_VPS_REGION: str = "us-east-1"

    STORAGE_CLOUD_ENDPOINT_URL: str | None = None
    STORAGE_CLOUD_PUBLIC_ENDPOINT_URL: str | None = None
    STORAGE_CLOUD_ACCESS_KEY: str | None = None
    STORAGE_CLOUD_SECRET_KEY: str | None = None
    STORAGE_CLOUD_BUCKET: str | None = None
    STORAGE_CLOUD_REGION: str = "us-east-1"

    # boto3 client tuning (large-file/many-object ingestion performance).
    # STORAGE_MAX_POOL_CONNECTIONS raises boto3's default HTTP connection
    # pool (10) so StorageService.put_many()'s concurrent uploads don't
    # serialize on pool checkout — measured directly against real MinIO
    # with a 2.03GB NetCDF's ~14,600-object Zarr store (see ingestion.py's
    # _upload_zarr_store docstring for the full benchmark): concurrency=16
    # was capped at ~80 files/s by the default pool of 10, and reached
    # ~110 files/s once the pool was widened to 32 — chosen as
    # concurrency*2 so every worker thread can always hold its own
    # connection with headroom, not tuned independently.
    STORAGE_MAX_POOL_CONNECTIONS: int = 32
    # boto3's built-in retry count for transient errors (connection reset,
    # timeout, 5xx) on every S3 call this client makes — covers Case 6
    # ("MinIO temporarily becomes unavailable") without any custom retry
    # loop, since this is exactly what botocore's standard retry mode
    # already does per-request.
    STORAGE_MAX_RETRIES: int = 3
    # Bounded worker count for StorageService.put_many()'s concurrent
    # object upload — NOT unlimited, so a store with tens of thousands of
    # chunk files can't spawn tens of thousands of threads. Benchmarked
    # directly (real MinIO, real ~55KB Zarr chunk files): throughput
    # plateaued around this value (16 threads / 32-connection pool ≈
    # 111 files/s) with no further gain measured at 32 threads on the
    # same pool size — this default reflects that plateau, not a guess.
    STORAGE_UPLOAD_CONCURRENCY: int = 16

    MAX_UPLOAD_SIZE_MB: int = 5000

    # Data Request supporting documents (PDF/DOC/DOCX justification
    # attachments) are a small user upload, not a scientific dataset file —
    # kept far below MAX_UPLOAD_SIZE_MB deliberately, per explicit product
    # requirement.
    MAX_SUPPORTING_DOCUMENT_SIZE_MB: int = 3

    # Legacy (pre-v7.3) MATLAB .mat files are read via scipy.io.loadmat,
    # which has no chunked/partial-read API — the whole file loads into
    # memory in one call regardless of size. v7.3 .mat files are HDF5
    # under the hood and CAN be read in slices (see mat_parser.py), so
    # this threshold only rejects the legacy format, with a clear message
    # pointing at the real alternative (re-save as v7.3 in MATLAB, the
    # default for files over 2GB there anyway) rather than attempting a
    # load that risks OOMing the ingestion worker.
    LEGACY_MAT_MAX_SIZE_MB: int = 2000

    # Fallback CRS for a gridded MATLAB struct (.mat) file whose X/Y
    # coordinates are clearly projected (large values, not lon/lat degree
    # range) and carry no embedded projection metadata — used only when
    # neither the file itself nor a future per-upload override specifies
    # one. UTM zone 46N covers Bangladesh's coastal operating area, which
    # is where every gridded-struct .mat file ingested so far originates.
    DEFAULT_PROJECTED_CRS: str = "EPSG:32646"

    # --- Ingestion worker timeouts (Phase 2) ---
    #
    # A single global timeout doesn't fit an ingestion job whose duration
    # scales with file size across a 50GB-TB range — these two settings
    # let the per-task soft time limit (set in celery_app.py, computed
    # per-dispatch from the actual file's size_bytes) scale instead of
    # being one fixed number. Both are configurable via env var rather
    # than hardcoded specifically because the "right" throughput assumption
    # depends on real deployment network/disk characteristics this
    # codebase has no way to know in advance — the values below are
    # documented starting assumptions, not measured production numbers.
    #
    # Baseline: a small/instant allowance so trivial files aren't held to
    # an unreasonably long deadline for no reason.
    INGESTION_SOFT_TIME_LIMIT_BASE_SECONDS: int = 300
    # Per-GB allowance added on top of the baseline, assuming a
    # deliberately conservative effective throughput (~50MB/s) that
    # covers download-from-MinIO + parse + convert + upload-processed-
    # artifact combined, not just raw network transfer — chosen to be
    # comfortably generous rather than tightly tuned, since the failure
    # mode of "cut off too early" (aborts real work) is worse than "runs
    # a bit long" for a background job with no user waiting synchronously.
    # Override via INGESTION_SOFT_TIME_LIMIT_SECONDS_PER_GB in .env once
    # this deployment's real measured throughput (disk + network + CPU on
    # the actual VPS, not this conservative assumption) is known — this is
    # the single knob most worth tuning if large-file ingestion is timing
    # out in practice on hardware this default doesn't fit.
    INGESTION_SOFT_TIME_LIMIT_SECONDS_PER_GB: int = 180
    # Optional upper ceiling on the computed soft limit — None (default)
    # means unlimited, so a genuinely TB-scale file is never capped below
    # what BASE + PER_GB×size would otherwise allow. Only meaningful to set
    # if a deployment wants a hard ceiling regardless of file size (e.g. "no
    # single ingestion job should ever be allowed to run past 24 hours");
    # deliberately NOT defaulted to a large fixed number, since that would
    # just be a differently-arbitrary hardcoded timeout instead of a real
    # per-size calculation.
    INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS: int | None = None
    # Hard worker-level failsafe (SIGKILL), NOT a business-logic timeout —
    # Celery's soft_time_limit above is what ingestion.py actually catches
    # and handles gracefully (cleanup + a clear FAILED message). This is a
    # backstop for the pathological case where a task somehow doesn't
    # respond to the soft signal at all (e.g. stuck in an uninterruptible
    # C-extension call with no Python bytecode boundary to catch
    # SoftTimeLimitExceeded at) — set comfortably above the soft limit so
    # it should never fire under normal operation; only exists so a
    # genuinely wedged worker process doesn't block that queue forever.
    INGESTION_HARD_TIME_LIMIT_GRACE_SECONDS: int = 600

    # Zarr write chunk size along the time axis (netcdf_parser.py /
    # mat_gridded_struct.py's shared _TIME_CHUNK_SIZE) — was 24, raised to
    # 200 after benchmarking against a real 2.03GB, 43,825-timestep, 8-
    # variable NetCDF (BoB_WaveData_2010_2024.nc): 24 produced 14,644
    # chunk objects (Zarr write itself 52.8s); 200 produced 1,788 (write
    # 18.0s) — strictly faster on EVERY query pattern tested too (narrow
    # time-range, full-range point series, spatial subset, resample-to-
    # daily, resample-to-monthly — fewer dask chunks means less task-graph
    # scheduling overhead, which dominates over "reads slightly more data
    # per chunk than the narrowest possible request"). No tradeoff found
    # at this dataset's scale; re-benchmark before raising further, since
    # a large enough chunk eventually does start over-reading for narrow
    # queries.
    INGESTION_ZARR_TIME_CHUNK_SIZE: int = 200

    # --- Subset extraction (Master Plan §3 Phase 5) ---
    EXTRACTION_DOWNLOAD_URL_EXPIRE_MINUTES: int = 60
    EXTRACTION_SYNC_THRESHOLD_MB: int = 10

    # --- Backups (Master Plan §3 Phase 10 task 4) ---
    # How long a fresh backup dump is kept before its retention sweep
    # (run at the end of every successful nightly backup, see
    # worker/tasks/backups.py) deletes it plus its storage object — keeps
    # backups/ from growing unbounded on a VPS with finite disk.
    BACKUP_RETENTION_DAYS: int = 30
    # Deliberately shorter than EXTRACTION_DOWNLOAD_URL_EXPIRE_MINUTES —
    # a backup dump is full production data, a strictly more sensitive
    # artifact than any single extraction's scoped subset.
    BACKUP_DOWNLOAD_URL_EXPIRE_MINUTES: int = 10
    # Hard ceiling on the pg_dump subprocess itself — a hung dump
    # (network partition to a remote DB host, disk contention) must not
    # leave a Celery worker slot occupied indefinitely; 1 hour is
    # generous for this project's current data scale and can be raised
    # if a real production DB genuinely needs longer.
    BACKUP_PG_DUMP_TIMEOUT_SECONDS: int = 3600

    # --- Monitoring (Master Plan §3 Phase 10 task 5) ---
    # Filesystem path shutil.disk_usage checks in admin_health_service.py.
    # Best-effort inside a container — the real signal is the HOST's disk
    # usage, which this can only approximate unless the volume backing
    # this path is bind-mounted from the host. VPS-level disk monitoring
    # (see docs/VPS_PROVISIONING.md's ongoing-operations section) is the
    # authoritative source in production; this check exists for a quick
    # at-a-glance signal in the admin dashboard, not as a replacement.
    DISK_USAGE_CHECK_PATH: str = "/"

    # --- Visualization engine (Master Plan §3 Phase 7) ---
    # Spatial interpolation requests at or below point_count * resolution^2
    # work units compute synchronously in-process; larger ones dispatch to
    # Celery. At the prototype's resolution options (20/40/60) and typical
    # station counts (~20-30), this keeps "low"/"medium" always sync and
    # only pushes "high" with many stations to a background job.
    VIZ_SPATIAL_SYNC_THRESHOLD_CELLS: int = 40_000

    # Visualize Performance plan, Phase 4: Statistics/Comparison requests
    # that touch non-legacy (Parquet/Zarr) files above this combined
    # file_size_bytes total dispatch to Celery instead of computing
    # in-process — same sync/async split as VIZ_SPATIAL_SYNC_THRESHOLD_
    # CELLS above, but sized by data volume rather than grid-cell count
    # since Statistics/Comparison have no equivalent "resolution" knob.
    # A request touching only legacy DatasetRecord rows (no Parquet/Zarr
    # files) always computes synchronously regardless of this threshold —
    # legacy SQL aggregation was never the expensive path this plan
    # targets. One shared setting for both endpoints since they hit the
    # same storage tiers with comparable per-byte cost.
    VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES: int = 200_000_000

    # --- PLAN.md Phase 5 concurrency limiter (per worker process) ---
    # Caps how many DuckDB (Parquet)/xarray (Zarr) queries run
    # concurrently INSIDE ONE Gunicorn worker process — every one of
    # these runs via asyncio.to_thread and is genuinely CPU-bound (a
    # real production-mode 4-worker benchmark measured backend container
    # CPU hitting ~1100% of the host's 12 cores under 50-500 concurrent
    # requests, with each DuckDB connection capped at 2 threads;
    # unbounded concurrency meant far more simultaneous queries than the
    # machine's real core count, which starved OTHER requests' Postgres
    # connections — even ones with no DuckDB work at all — of CPU time
    # to release them, exhausting the pool).
    #
    # Empirically tuned via direct A/B testing at concurrency=200 against
    # this exact benchmark (200K-row Parquet dataset, real MinIO), not
    # chosen a priori:
    #   8/worker (32 system-wide): CPU still ~1100%, pool errors persist.
    #   1/worker (4 system-wide):  MORE errors (73/600) — the semaphore
    #                              wait itself becomes long enough that
    #                              connections still time out waiting on
    #                              the pool checkout (30s default).
    #   2/worker (8 system-wide):  best measured tradeoff — 3/600 errors
    #                              (down from 328/800 unbounded), though
    #                              still real degradation at high
    #                              concurrency (p50 ~23s at 200
    #                              concurrent) — see PLAN.md Phase 5's
    #                              concurrency benchmark section for the
    #                              full sweep and the honest conclusion
    #                              about what concurrency level this
    #                              configuration actually sustains.
    #
    # Re-benchmarked 2026-08-21 (Visualize Performance plan, Phase 6) after
    # Phases 0-5 cut Statistics/Comparison's uncached-request cost and
    # diverted genuinely heavy requests off the synchronous path entirely
    # — same methodology (real 4-Gunicorn-worker container, real Postgres/
    # MinIO, concurrency=200, 600 requests against /catalog/{id}/records
    # on a 1.68M-row Parquet-backed dataset, larger than the original
    # 200K-row target):
    #   unbounded: 0/600 errors, p50=12.2s, p95=19.4s — CPU peaked ~370%
    #              of the host's 12 cores (well under the original's
    #              ~1100% saturation — confirms the load profile is
    #              genuinely lighter now, not just re-measured noise).
    #   1/worker:  2/600 errors, p50=9.8s, p95=22.2s.
    #   2/worker:  0/600 errors, p50=9.9s, p95=17.2s — still the best or
    #              tied-best on every metric measured.
    #   8/worker:  0/600 errors, p50=10.1s, p95=17.4s.
    # 2 remains the right value — left unchanged rather than raised
    # speculatively, since it was still optimal-or-tied even under a
    # genuinely lighter, less CPU-saturated load than the original
    # benchmark measured.
    #
    # This does not add a queue with unlimited depth — a request that
    # can't acquire a slot waits on the semaphore itself (still holding
    # its Postgres connection while it waits, which is the residual
    # limitation — see DB_POOL_SIZE's docstring for the complementary
    # fix), so total in-flight CPU-bound work is bounded by (workers x
    # this value), not by whatever the OS scheduler happens to allow.
    QUERY_CONCURRENCY_LIMIT_PER_WORKER: int = 2

    # DuckDB's own SET threads=N per connection (tabular_query_service.py)
    # — the direct lever on DuckDB's internal query-execution parallelism,
    # separate from QUERY_CONCURRENCY_LIMIT_PER_WORKER above (which bounds
    # how many DuckDB CALLS run concurrently, not how many threads each
    # one uses). Re-benchmarked at 1 vs 2 against the real production-mode
    # 4-worker setup: backend CPU stayed at ~1100% either way — DuckDB's
    # httpfs/S3 extension appears to use additional OS threads for network
    # I/O beyond what this compute-thread setting bounds, so lowering it
    # further did not measurably change peak CPU in this benchmark. Left
    # at 2 (not reduced to 1) since 1 showed no CPU benefit but should
    # only ever slow down each individual query's own execution.
    QUERY_DUCKDB_THREADS_PER_CONNECTION: int = 2

    # --- Email (SMTP-compatible transactional provider; swappable) ---
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_TLS: bool = True
    EMAIL_FROM_ADDRESS: EmailStr = "no-reply@bodp.example.org"
    EMAIL_FROM_NAME: str = "BODP Platform"

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _warn_default_secret(cls, v: str) -> str:
        return v

    @field_validator("INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS", mode="before")
    @classmethod
    def _empty_string_means_unset(cls, v: object) -> object:
        # docker-compose.prod.yml's ${VAR:-} syntax always sets the env key,
        # to an empty string when the operator hasn't set a real value —
        # there's no way to conditionally omit an environment: entry from
        # that YAML syntax. An empty string must mean "unlimited" (the same
        # as never having set the var at all), not a validation error.
        return None if v == "" else v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
