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


def extract_key(grant_id: uuid.UUID | str, extraction_id: uuid.UUID | str, extension: str) -> str:
    ext = extension.lstrip(".")
    return f"extracts/{grant_id}/{extraction_id}.{ext}"


def previews_key(dataset_id: uuid.UUID | str, filename: str = "thumbnail.png") -> str:
    return f"previews/{dataset_id}/{sanitize_filename(filename)}"
