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
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

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

    MAX_UPLOAD_SIZE_MB: int = 5000

    # Legacy (pre-v7.3) MATLAB .mat files are read via scipy.io.loadmat,
    # which has no chunked/partial-read API — the whole file loads into
    # memory in one call regardless of size. v7.3 .mat files are HDF5
    # under the hood and CAN be read in slices (see mat_parser.py), so
    # this threshold only rejects the legacy format, with a clear message
    # pointing at the real alternative (re-save as v7.3 in MATLAB, the
    # default for files over 2GB there anyway) rather than attempting a
    # load that risks OOMing the ingestion worker.
    LEGACY_MAT_MAX_SIZE_MB: int = 2000

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
    INGESTION_SOFT_TIME_LIMIT_SECONDS_PER_GB: int = 180

    # --- Subset extraction (Master Plan §3 Phase 5) ---
    EXTRACTION_DOWNLOAD_URL_EXPIRE_MINUTES: int = 60
    EXTRACTION_SYNC_THRESHOLD_MB: int = 10

    # --- Visualization engine (Master Plan §3 Phase 7) ---
    # Spatial interpolation requests at or below point_count * resolution^2
    # work units compute synchronously in-process; larger ones dispatch to
    # Celery. At the prototype's resolution options (20/40/60) and typical
    # station counts (~20-30), this keeps "low"/"medium" always sync and
    # only pushes "high" with many stations to a background job.
    VIZ_SPATIAL_SYNC_THRESHOLD_CELLS: int = 40_000

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

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
