import re
import uuid

# Matches the bucket organization specified in Master Plan §2 / §3 Phase 2
# task 2 (carried over from the original architecture doc's s3://ocean-data/
# layout): raw/, processed/, extracts/, previews/ — segregated by purpose so
# lifecycle policies and access rules can differ per prefix later.

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(original_name: str) -> str:
    """Strip path separators and any character outside a safe allowlist so a
    crafted filename (e.g. containing `../`) can never influence the storage
    key layout — the object key is always {prefix}/{uuid}/{safe_name}, never
    built from unsanitized user input alone."""
    name = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip("._")
    return name or "file"


def raw_key(dataset_id: uuid.UUID | str, file_id: uuid.UUID | str, original_name: str) -> str:
    """Key for a single uploaded raw object.

    Zarr readiness note: a Zarr store is a directory of many small chunk
    files, not one object, so it doesn't fit this single-key function as-is.
    The natural extension is a `raw_prefix()` helper returning
    `raw/{dataset_id}/{file_id}/` under which the whole chunk tree is
    uploaded — every existing key stays valid since it's a different
    function, not a change to this one. The simpler near-term bridge is
    accepting a `.zarr.zip` container (a single object zarr's own tooling
    can read directly), which needs no storage-layer change at all, only a
    new FileParser. Either path is additive; nothing here needs to change
    to support it later.
    """
    return f"raw/{dataset_id}/{file_id}_{sanitize_filename(original_name)}"


def processed_key(
    dataset_id: uuid.UUID | str, file_id: uuid.UUID | str, extension: str = "parquet"
) -> str:
    """Key for the query-optimized processed/ artifact. Extension is
    parser-defined — tabular formats produce "parquet", GeoTIFF produces
    "cog.tif". Defaults to "parquet" so existing call sites are unaffected."""
    ext = extension.lstrip(".")
    return f"processed/{dataset_id}/{file_id}.{ext}"


def processed_prefix(dataset_id: uuid.UUID | str, file_id: uuid.UUID | str) -> str:
    """Prefix for a multi-object processed/ artifact (PLAN.md Phase 5) —
    a Zarr store is a directory of many small chunk + metadata files, not
    one blob, so GRIDDED data is uploaded as individual objects under
    this shared prefix (`processed/{dataset_id}/{file_id}.zarr/...`)
    rather than one zipped object. This is exactly the `raw_key()`
    docstring's anticipated "raw_prefix()-style multi-object key"
    extension, applied to the processed/ tier instead — enables genuine
    chunk-range reads via ordinary per-object GETs (xarray/zarr/fsspec
    talking to the S3-compatible backend directly), which a single
    zipped object never could without a full-object download first."""
    return f"processed/{dataset_id}/{file_id}.zarr"


def extract_key(grant_id: uuid.UUID | str, extraction_id: uuid.UUID | str, extension: str) -> str:
    ext = extension.lstrip(".")
    return f"extracts/{grant_id}/{extraction_id}.{ext}"


def previews_key(dataset_id: uuid.UUID | str, filename: str = "thumbnail.png") -> str:
    return f"previews/{dataset_id}/{sanitize_filename(filename)}"


def snapshot_key(dataset_id: uuid.UUID | str) -> str:
    """Key for a dataset's precomputed default-view snapshot (Dataset
    Default-View Snapshot feature) — a small JSON blob (sample rows +
    DatasetVariable stats + one default chart) regenerated on every
    successful ingestion, whose freshness is tracked by
    Dataset.snapshot_version vs Dataset.version, never by a version
    number baked into the key itself: overwriting the same key each
    regeneration is intentional (a stale object is never orphaned or
    read once its pointer column no longer matches)."""
    return f"snapshots/{dataset_id}/snapshot.json"


def avatar_key(user_id: uuid.UUID | str, filename: str) -> str:
    """Key for a user's profile avatar image. Stored as a bare object key
    (like every other *_key column), never a full URL — the frontend
    resolves it to a fetchable address via the public storage endpoint
    base at render time, the same way presigned-download URLs are built
    fresh rather than persisted."""
    return f"avatars/{user_id}/{sanitize_filename(filename)}"


def supporting_document_key(
    request_id: uuid.UUID | str, document_id: uuid.UUID | str, original_name: str
) -> str:
    """Key for a user-uploaded Data Request supporting document (PDF/DOC/
    DOCX). Private — never served from a public bucket/prefix, only via a
    presigned URL issued to an authorized admin. Keyed by both the owning
    request and the document's own id (not just the sanitized filename) so
    the key is non-guessable even if two requests upload identically named
    files."""
    return f"supporting-documents/{request_id}/{document_id}_{sanitize_filename(original_name)}"


def media_key(media_id: uuid.UUID | str, filename: str) -> str:
    """Key for a Media Library asset (Master Plan §3 Phase 9) — the single
    source of truth for blog featured images, about-team photos, and any
    other admin-uploaded content asset. Public-readable, like avatars."""
    return f"media/{media_id}/{sanitize_filename(filename)}"


def backup_key(backup_id: uuid.UUID | str, kind: str = "pg_dump") -> str:
    """Key for a point-in-time recoverable backup artifact (Master Plan
    §3 Phase 10 task 4). `kind` distinguishes the Postgres dump from a
    future object-storage-tier backup, both segregated under this same
    prefix by kind rather than a separate top-level prefix, since both
    are the same operational concept (a recoverable artifact) at
    different scope. Private — like supporting-documents/, never served
    from a public bucket/prefix; only presigned, and with a shorter
    expiry than most other presigned downloads, since a backup dump
    contains full production data."""
    return f"backups/{backup_id}/{kind}.dump"
