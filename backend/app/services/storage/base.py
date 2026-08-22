from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, NamedTuple


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


class UploadItem(NamedTuple):
    """One file destined for put_many() — a local path and its target
    key, not the file's bytes. put_many() implementations open each path
    only when that item's own upload actually runs, so queuing thousands
    of items never holds thousands of file handles or any file content in
    memory at once."""

    local_path: Path
    key: str


class UploadResult(NamedTuple):
    """Per-item outcome from put_many() — always exactly one of these per
    input UploadItem, success or failure, so a caller can tell a total
    failure (nothing uploaded) from a partial failure (some objects now
    exist in storage, some don't) and clean up accordingly."""

    key: str
    ok: bool
    error: str | None = None


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

    def put_many(
        self,
        bucket: str,
        items: Iterable[UploadItem],
        *,
        content_type: str | None = None,
        concurrency: int = 1,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[UploadResult]:
        """Uploads many local files to `bucket`, one object per UploadItem
        — the many-small-object case a Zarr store's chunk files are (see
        ingestion.py's processed-artifact upload). Each item's bytes are
        only ever read from its own `local_path` at the moment that one
        item's upload runs (via `put()`, which itself streams) — `items`
        can be an arbitrarily large iterable without the caller or this
        method ever holding more than `concurrency` files' worth of bytes
        open at once.

        Every item gets exactly one UploadResult (never raises for a
        single item's failure — errors are reported, not propagated) so a
        caller can distinguish "some objects now exist in storage, some
        don't" from a clean total failure and clean up precisely.
        `on_progress(completed_count, total_count)`, if given, is called
        after every completed item (success or failure) — throttling how
        often that translates into a DB write is the caller's job, not
        this method's.

        Default implementation: sequential (concurrency is accepted for
        interface consistency but ignored) — correct for any backend,
        just not fast for one with thousands of small objects. The only
        current backend (S3CompatibleBackend) overrides this with a real
        bounded-concurrency upload; this fallback exists so a future
        backend is never required to implement concurrency to be usable."""
        items = list(items)
        results: list[UploadResult] = []
        for item in items:
            try:
                with open(item.local_path, "rb") as f:
                    self.put(bucket, item.key, f, content_type=content_type)
                results.append(UploadResult(key=item.key, ok=True))
            except Exception as exc:
                results.append(UploadResult(key=item.key, ok=False, error=str(exc)))
            if on_progress is not None:
                on_progress(len(results), len(items))
        return results

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
    def delete_prefix(self, bucket: str, prefix: str) -> None:
        """Removes every object under `prefix` (PLAN.md Phase 5 — a Zarr
        store is many small chunk/metadata objects under a shared prefix,
        not one key, so cancellation/retry cleanup needs a prefix-scoped
        delete rather than a single delete() call). Must not raise if no
        objects exist under the prefix (idempotent, same contract as
        delete())."""

    @abstractmethod
    def exists(self, bucket: str, key: str) -> bool:
        ...

    @abstractmethod
    def list_keys(self, bucket: str, prefix: str) -> Iterable[str]:
        """Yields every object key under `prefix`, paginated internally so
        an arbitrarily large prefix is never loaded into memory at once
        (Phase 10.5 — the orphan sweep's storage-to-DB direction, the one
        real list-objects need in the app; every other call site works
        with keys it already knows, so this stays the only consumer)."""

    @abstractmethod
    def stat(self, bucket: str, key: str) -> StorageObject | None:
        """Return metadata (size, etag, content-type) without downloading
        the object body, or None if it doesn't exist."""

    @abstractmethod
    def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it doesn't already exist. Called at startup
        / first use, never assumed to be a manual provisioning step."""

    @abstractmethod
    def set_public_prefix_policy(self, bucket: str, prefix: str) -> None:
        """Grants anonymous GET access to objects under `prefix` only —
        everything else in the bucket (dataset files, extraction outputs)
        stays private and reachable only via presign_get. Used for avatars,
        which are rendered as plain <img src> URLs rather than presigned
        links. Idempotent — safe to call on every upload."""

    # --- Direct-to-storage multipart upload (Phase 1: large-file uploads) ---
    #
    # `put()` above already does an *implicit*, backend-managed multipart
    # transfer for anything the boto3 client itself streams — but that still
    # proxies every byte through the FastAPI process. These five methods
    # expose the *real* S3 multipart lifecycle so a browser can PUT parts
    # directly to storage using short-lived presigned URLs, with the backend
    # only ever handling small JSON control-plane requests.

    @abstractmethod
    def create_multipart_upload(
        self, bucket: str, key: str, *, content_type: str | None = None
    ) -> str:
        """Starts a multipart upload session and returns its upload ID —
        the token every subsequent part/complete/abort call is scoped to."""

    @abstractmethod
    def presign_upload_part(
        self,
        bucket: str,
        key: str,
        *,
        upload_id: str,
        part_number: int,
        expires_in_seconds: int = 3600,
    ) -> str:
        """A temporary, signed URL allowing the client to PUT exactly one
        part's bytes directly to storage. part_number is 1-indexed per the
        S3 multipart API."""

    @abstractmethod
    def complete_multipart_upload(
        self, bucket: str, key: str, *, upload_id: str, parts: list[dict]
    ) -> StorageObject:
        """Finalizes the upload once every part has been PUT. `parts` is a
        list of {"PartNumber": int, "ETag": str} in ascending part order —
        the ETags the client observed from each part's PUT response,
        required so storage can verify nothing was corrupted/reordered."""

    @abstractmethod
    def abort_multipart_upload(self, bucket: str, key: str, *, upload_id: str) -> None:
        """Cancels an in-progress multipart session and releases any parts
        already uploaded — must not raise if the session is already gone
        (idempotent, safe to call from a cleanup/cancel path)."""

    @abstractmethod
    def list_parts(self, bucket: str, key: str, *, upload_id: str) -> list[dict]:
        """Returns the parts storage has actually received for this session
        — {"PartNumber": int, "ETag": str, "Size": int} each — used to
        reconcile real upload progress against what the client claims."""
