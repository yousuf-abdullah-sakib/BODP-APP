from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO


class StorageBackendError(Exception):
    """Raised for any storage operation failure (network, auth, missing object).

    Callers should catch this rather than boto3's ClientError directly, so
    the rest of the app never needs to know which concrete backend/provider
    is in use — the whole point of the abstraction (Master Plan §2).
    """


@dataclass(frozen=True)
class StorageObject:
    bucket: str
    key: str
    size_bytes: int
    etag: str | None = None
    content_type: str | None = None


class StorageService(ABC):
    """Backend-agnostic object storage interface.

    Two concrete implementations exist: the VPS-local MinIO instance and a
    generic S3-compatible cloud backend (AWS S3 / Backblaze B2 / Cloudflare
    R2 / Wasabi / GCS-via-S3-interop — provider undecided per Master Plan
    §0/§5). Every read/write in the app goes through this interface so a
    file's physical location (`dataset_files.storage_backend`) is invisible
    to everything above this layer.
    """

    @abstractmethod
    def put(
        self,
        bucket: str,
        key: str,
        data: BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> StorageObject:
        """Stream-upload `data` to `bucket/key`. Must not buffer the whole
        file in memory — large NetCDF/CSV uploads are expected."""

    @abstractmethod
    def get(self, bucket: str, key: str) -> BinaryIO:
        """Return a readable stream for `bucket/key`. Caller is responsible
        for closing it."""

    @abstractmethod
    def presign_get(self, bucket: str, key: str, *, expires_in_seconds: int = 3600) -> str:
        """A temporary, signed URL granting GET access without exposing
        credentials or requiring the bucket to be public."""

    @abstractmethod
    def presign_put(self, bucket: str, key: str, *, expires_in_seconds: int = 3600) -> str:
        """A temporary, signed URL allowing a client to upload directly to
        the bucket without proxying the bytes through the API process."""

    @abstractmethod
    def delete(self, bucket: str, key: str) -> None:
        """Remove an object. Must not raise if the object is already absent
        (idempotent — safe to call from a cleanup/cascade path)."""

    @abstractmethod
    def exists(self, bucket: str, key: str) -> bool:
        ...

    @abstractmethod
    def stat(self, bucket: str, key: str) -> StorageObject | None:
        """Return metadata (size, etag, content-type) without downloading
        the object body, or None if it doesn't exist."""

    @abstractmethod
    def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it doesn't already exist. Called at startup
        / first use, never assumed to be a manual provisioning step."""
