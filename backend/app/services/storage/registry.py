from functools import lru_cache

from app.core.config import settings
from app.models.catalog import StorageBackend
from app.services.storage.base import StorageService
from app.services.storage.s3_backend import S3CompatibleBackend


class StorageNotConfiguredError(Exception):
    """Raised when code asks for the cloud backend but no cloud provider has
    been configured yet (Master Plan §5: provider selection is deferred)."""


@lru_cache
def _vps_backend() -> S3CompatibleBackend:
    return S3CompatibleBackend(
        endpoint_url=settings.STORAGE_VPS_ENDPOINT_URL,
        access_key=settings.STORAGE_VPS_ACCESS_KEY,
        secret_key=settings.STORAGE_VPS_SECRET_KEY,
        region=settings.STORAGE_VPS_REGION,
        name="vps_minio",
    )


@lru_cache
def _cloud_backend() -> S3CompatibleBackend:
    if not (
        settings.STORAGE_CLOUD_ENDPOINT_URL
        and settings.STORAGE_CLOUD_ACCESS_KEY
        and settings.STORAGE_CLOUD_SECRET_KEY
    ):
        raise StorageNotConfiguredError(
            "Cloud storage tier is not configured (STORAGE_CLOUD_* env vars unset). "
            "Provider selection is deferred per Master Plan §5."
        )
    return S3CompatibleBackend(
        endpoint_url=settings.STORAGE_CLOUD_ENDPOINT_URL,
        access_key=settings.STORAGE_CLOUD_ACCESS_KEY,
        secret_key=settings.STORAGE_CLOUD_SECRET_KEY,
        region=settings.STORAGE_CLOUD_REGION,
        name="cloud",
    )


def get_storage_backend(backend: str) -> StorageService:
    """Resolve a `dataset_files.storage_backend` value to the concrete
    StorageService instance that owns it. This is the ONLY place in the app
    that should branch on storage_backend — everywhere else works purely
    against the StorageService interface."""
    if backend == StorageBackend.VPS_MINIO.value:
        return _vps_backend()
    if backend == StorageBackend.CLOUD.value:
        return _cloud_backend()
    raise ValueError(f"Unknown storage backend: {backend!r}")


def default_bucket_for(backend: str) -> str:
    if backend == StorageBackend.VPS_MINIO.value:
        return settings.STORAGE_VPS_BUCKET
    if backend == StorageBackend.CLOUD.value:
        if not settings.STORAGE_CLOUD_BUCKET:
            raise StorageNotConfiguredError("STORAGE_CLOUD_BUCKET is not set")
        return settings.STORAGE_CLOUD_BUCKET
    raise ValueError(f"Unknown storage backend: {backend!r}")
