"""Orphan sweep (Master Plan §3 Phase 10 task 8, Phase 10.5 of the
hardening plan): finds storage-key/DB-row mismatches in both directions.

DB-to-storage direction (a DB row references a key that doesn't exist in
storage) is the more actionable one — a DatasetFile whose storage_key
resolves to nothing means a broken dataset any granted user could 404 on.
Storage-to-DB direction (an object exists with no DB row pointing at it)
is wasted disk space, typically from a crashed task that uploaded before
its DB write committed.

Report-only by default (Celery task and the shared core function never
delete anything) — a false positive here (e.g. a key that exists because
a DB transaction hasn't committed yet, racing the sweep) would be a real,
if rare, data-loss risk if auto-deleted. Only the manual script variant
(app/scripts/orphan_sweep.py) can delete, and only with an explicit
--delete flag and explicit key list, mirroring cleanup_orphaned_upload.py's
own dry-run-by-default posture.

Six storage-key-bearing tables are covered: DatasetFile (raw AND
processed — the latter lives in file_metadata JSONB, not a real column),
MediaFile, RequestSupportingDocument, SubsetExtraction (output only —
in-progress rows have no output yet), Report (output_storage_key, no
bucket/backend column — always vps_minio, matching reports.py/
admin_reports_service.py's own hardcoded assumption), Backup
(storage_key, same vps_minio assumption as Report, matching backups.py).

snapshots/ and previews/ are deliberately excluded from storage-to-DB
"orphan" findings (see snapshot_key's own docstring: overwriting the same
key on every regeneration is intentional, so an old snapshot object with
no matching Dataset.snapshot_version is expected, not a bug; previews/ is
simply unused dead code today) but are still listed for total-size
visibility.
"""

from dataclasses import dataclass, field

import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.models.admin import Backup, MediaFile, Report
from app.models.catalog import DatasetFile
from app.models.requests import RequestSupportingDocument, SubsetExtraction
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Every backend the app can write to today (app/models/catalog.py's
# StorageBackend enum) — swept regardless of which one a given
# DatasetFile actually chose, since the sweep's job is to find drift, not
# assume the "one backend per dataset" comment in extraction.py always
# holds.
_ALL_BACKENDS = ["vps_minio", "cloud"]

# Prefixes never written to a backend other than vps_minio in current
# code (see keys.py call-site survey backing this phase) — reports/ has
# no keys.py helper at all (its key is built inline in
# worker/tasks/reports.py), so it's listed by its literal prefix string
# rather than a function reference. snapshots/ is deliberately NOT here —
# it's swept separately via _REGENERATED_PREFIXES below, since it's
# excluded from orphan findings entirely, not just vps-only.
_VPS_ONLY_PREFIXES = ["avatars/", "supporting-documents/", "media/", "backups/", "reports/"]

# raw/ and processed/ are backend-parametric (DatasetFile.storage_backend
# / extraction's files[0].storage_backend) — swept on every configured
# backend rather than assumed to be vps_minio-only.
_BACKEND_PARAMETRIC_PREFIXES = ["raw/", "processed/", "extracts/"]

# Excluded from storage-to-DB "orphan" reporting for the documented
# reason above — still counted for total-size visibility only.
_REGENERATED_PREFIXES = {"snapshots/", "previews/"}


@dataclass
class KnownKey:
    backend: str
    bucket: str
    key: str
    source: str  # e.g. "DatasetFile.storage_key:<uuid>" — for a readable finding


@dataclass
class OrphanFinding:
    direction: str  # "db_to_storage" or "storage_to_db"
    backend: str
    bucket: str
    key: str
    detail: str


@dataclass
class SweepResult:
    findings: list[OrphanFinding] = field(default_factory=list)
    known_key_count: int = 0
    listed_object_count: int = 0
    regenerated_prefix_object_count: int = 0


def _configured_backends() -> list[str]:
    configured = []
    for name in _ALL_BACKENDS:
        try:
            get_storage_backend(name)
            default_bucket_for(name)
            configured.append(name)
        except Exception:
            continue
    return configured


def collect_known_keys(db) -> list[KnownKey]:
    """One DB pass per table, building the complete set of keys any
    current row actually points at — loaded once per sweep run (not
    per-key-query) to avoid an N+1 round trip against potentially
    thousands of objects."""
    known: list[KnownKey] = []

    for f in db.execute(select(DatasetFile)).scalars().all():
        known.append(KnownKey(f.storage_backend, f.storage_bucket, f.storage_key, f"DatasetFile.storage_key:{f.id}"))
        meta = f.file_metadata or {}
        processed_bucket = meta.get("processed_bucket")
        if processed_bucket:
            processed_key = meta.get("processed_key")
            processed_prefix = meta.get("processed_prefix")
            if processed_key:
                known.append(
                    KnownKey(f.storage_backend, processed_bucket, processed_key, f"DatasetFile.processed_key:{f.id}")
                )
            elif processed_prefix:
                # A Zarr store's processed prefix isn't one key — every
                # object currently listed under it is treated as known,
                # resolved separately below via processed/ prefix
                # scanning rather than an exact-key equality check.
                known.append(
                    KnownKey(
                        f.storage_backend,
                        processed_bucket,
                        processed_prefix.rstrip("/") + "/",
                        f"DatasetFile.processed_prefix:{f.id}",
                    )
                )

    for m in db.execute(select(MediaFile)).scalars().all():
        known.append(KnownKey(m.storage_backend, m.storage_bucket, m.storage_key, f"MediaFile.storage_key:{m.id}"))

    for d in db.execute(select(RequestSupportingDocument)).scalars().all():
        known.append(
            KnownKey(d.storage_backend, d.storage_bucket, d.storage_key, f"RequestSupportingDocument.storage_key:{d.id}")
        )

    for e in db.execute(select(SubsetExtraction)).scalars().all():
        if e.output_file_key and e.output_bucket and e.output_storage_backend:
            known.append(
                KnownKey(
                    e.output_storage_backend, e.output_bucket, e.output_file_key, f"SubsetExtraction.output_file_key:{e.id}"
                )
            )

    for r in db.execute(select(Report)).scalars().all():
        if r.output_storage_key:
            known.append(KnownKey("vps_minio", default_bucket_for("vps_minio"), r.output_storage_key, f"Report.output_storage_key:{r.id}"))

    for b in db.execute(select(Backup)).scalars().all():
        if b.storage_key:
            known.append(KnownKey("vps_minio", default_bucket_for("vps_minio"), b.storage_key, f"Backup.storage_key:{b.id}"))

    return known


def check_db_to_storage(known_keys: list[KnownKey]) -> list[OrphanFinding]:
    """For every non-prefix known key, confirm the object actually exists
    in storage. A prefix entry (Zarr processed artifacts) is checked via
    exists() against nothing meaningful — skipped here, since prefix
    completeness is a many-object question the storage-to-DB direction's
    listing already covers well enough for this sweep's purpose (a
    partially-uploaded Zarr store is already handled by the ingestion
    task's own all-or-nothing ZarrUploadFailed cleanup, not this sweep)."""
    findings: list[OrphanFinding] = []
    for kk in known_keys:
        if kk.key.endswith("/"):
            continue
        try:
            storage = get_storage_backend(kk.backend)
            if not storage.exists(kk.bucket, kk.key):
                findings.append(
                    OrphanFinding(
                        direction="db_to_storage",
                        backend=kk.backend,
                        bucket=kk.bucket,
                        key=kk.key,
                        detail=f"Referenced by {kk.source} but missing from storage",
                    )
                )
        except Exception:
            logger.exception("integrity.db_to_storage_check_failed", backend=kk.backend, bucket=kk.bucket, key=kk.key)
    return findings


def check_storage_to_db(known_keys: list[KnownKey]) -> tuple[list[OrphanFinding], int, int]:
    """Lists every object under every prefix convention on every backend
    that convention can land on, flagging any key with no matching known
    key. Returns (findings, total_listed, regenerated_prefix_count)."""
    known_by_backend_bucket: dict[tuple[str, str], set[str]] = {}
    for kk in known_keys:
        known_by_backend_bucket.setdefault((kk.backend, kk.bucket), set()).add(kk.key)

    findings: list[OrphanFinding] = []
    total_listed = 0
    regenerated_count = 0
    configured = _configured_backends()

    def sweep_prefix(backend: str, prefix: str) -> None:
        nonlocal total_listed, regenerated_count
        try:
            bucket = default_bucket_for(backend)
            storage = get_storage_backend(backend)
        except Exception:
            return
        known_keys_for_bucket = known_by_backend_bucket.get((backend, bucket), set())
        known_prefixes_for_bucket = {k for k in known_keys_for_bucket if k.endswith("/")}
        try:
            for key in storage.list_keys(bucket, prefix):
                total_listed += 1
                if prefix in _REGENERATED_PREFIXES:
                    regenerated_count += 1
                    continue
                if key in known_keys_for_bucket:
                    continue
                if any(key.startswith(p) for p in known_prefixes_for_bucket):
                    continue
                findings.append(
                    OrphanFinding(
                        direction="storage_to_db",
                        backend=backend,
                        bucket=bucket,
                        key=key,
                        detail="Exists in storage but no DB row references it",
                    )
                )
        except Exception:
            logger.exception("integrity.storage_to_db_list_failed", backend=backend, bucket=bucket, prefix=prefix)

    for backend in configured:
        for prefix in _BACKEND_PARAMETRIC_PREFIXES:
            sweep_prefix(backend, prefix)

    # vps-only prefixes are only ever meaningful on vps_minio — sweeping
    # them on "cloud" too would just find nothing every run, for no
    # benefit, since nothing in current code ever writes there.
    if "vps_minio" in configured:
        for prefix in _VPS_ONLY_PREFIXES + sorted(_REGENERATED_PREFIXES):
            sweep_prefix("vps_minio", prefix)

    return findings, total_listed, regenerated_count


def run_sweep() -> SweepResult:
    with get_sync_db() as db:
        known_keys = collect_known_keys(db)

    result = SweepResult(known_key_count=len(known_keys))
    result.findings.extend(check_db_to_storage(known_keys))
    storage_findings, listed, regenerated = check_storage_to_db(known_keys)
    result.findings.extend(storage_findings)
    result.listed_object_count = listed
    result.regenerated_prefix_object_count = regenerated

    for f in result.findings:
        logger.warning(
            "integrity.orphan_found",
            direction=f.direction,
            backend=f.backend,
            bucket=f.bucket,
            key=f.key,
            detail=f.detail,
        )

    return result


@celery_app.task(name="integrity.orphan_sweep")
def orphan_sweep() -> dict:
    """Report-only — never deletes. Findings land in this task's Celery
    result (retrievable via the result backend) and as structured warning
    logs, one per orphan, so a real corruption/drift incident is
    discoverable without an admin needing to know this task exists in
    advance."""
    result = run_sweep()
    logger.info(
        "integrity.sweep_completed",
        finding_count=len(result.findings),
        known_key_count=result.known_key_count,
        listed_object_count=result.listed_object_count,
        regenerated_prefix_object_count=result.regenerated_prefix_object_count,
    )
    return {
        "finding_count": len(result.findings),
        "known_key_count": result.known_key_count,
        "listed_object_count": result.listed_object_count,
        "regenerated_prefix_object_count": result.regenerated_prefix_object_count,
        "findings": [
            {"direction": f.direction, "backend": f.backend, "bucket": f.bucket, "key": f.key, "detail": f.detail}
            for f in result.findings
        ],
    }
