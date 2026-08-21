"""Real automated pg_dump backups (Master Plan §3 Phase 10 task 4).

Runs pg_dump as a subprocess against the same database DATABASE_URL_SYNC
already points at, uploads the resulting dump to storage via
backup_key(), and updates the Backup row's status/size_bytes/
storage_key — mirrors reports.py's generate_report shape (sync DB
session via get_sync_db(), same as admin_stats.capture_daily_snapshot),
but with a real "running" -> "success"/"failed" lifecycle Report never
needed (a report either exists or doesn't; a backup can genuinely fail
mid-dump and must say so, not just silently never finish).

Security note: the database password is passed to the pg_dump subprocess
via the PGPASSWORD environment variable, never as a command-line
argument — a command-line password would be visible in `ps` output and
in Celery's own task-argument logging, which the connection URL as a
whole (including its embedded password) already isn't logged for this
exact reason (see how DATABASE_URL_SYNC itself is never logged verbatim
anywhere in this codebase).
"""
import os
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_sync_db
from app.models.admin import Backup, BackupStatus
from app.services.storage.keys import backup_key
from app.services.storage.registry import default_bucket_for, get_storage_backend
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)

_STORAGE_BACKEND = "vps_minio"

# Kept in sync with app/scripts/test_backup_restore.py's own
# _SANITY_TABLES — a handful of core tables whose row counts, captured
# HERE at dump time, let the restore-verification script compare a
# restored copy against what was actually in the dump, not a live query
# run later against a database that keeps changing underneath it.
_SANITY_TABLES = ["users", "datasets", "dataset_files"]


def _capture_row_counts(db) -> dict[str, int]:
    from sqlalchemy import text

    counts = {}
    for table in _SANITY_TABLES:
        result = db.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608 - fixed internal table list, never user input
        counts[table] = result.scalar_one()
    return counts


def _run_pg_dump(dump_path: Path) -> None:
    """Runs pg_dump (custom format, -Fc) against DATABASE_URL_SYNC's own
    target — the same database this whole app talks to — into dump_path.
    Raises subprocess.CalledProcessError on a non-zero exit, with stderr
    captured in the exception for the caller to log/store."""
    parsed = urlparse(settings.DATABASE_URL_SYNC)
    # urlparse's scheme for "postgresql+psycopg://..." is
    # "postgresql+psycopg" — pg_dump itself needs none of that, only the
    # host/port/user/dbname components, which parse identically regardless
    # of the SQLAlchemy driver suffix.
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    user = parsed.username or "postgres"
    password = parsed.password or ""
    dbname = parsed.path.lstrip("/") or "bodp"

    env = {**os.environ, "PGPASSWORD": password}
    subprocess.run(
        [
            "pg_dump",
            "--host", host,
            "--port", str(port),
            "--username", user,
            "--dbname", dbname,
            "--format", "custom",
            "--file", str(dump_path),
        ],
        env=env,
        capture_output=True,
        check=True,
        timeout=settings.BACKUP_PG_DUMP_TIMEOUT_SECONDS,
    )


def _sweep_expired_backups(db, storage) -> int:
    """Deletes Backup rows (plus their storage objects) older than
    BACKUP_RETENTION_DAYS — run at the end of every successful backup,
    since that's the one place a full row list is already cheaply
    available (no separate scheduled task needed just for this)."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.BACKUP_RETENTION_DAYS)
    expired = db.execute(select(Backup).where(Backup.started_at < cutoff)).scalars().all()
    for old_backup in expired:
        if old_backup.storage_key:
            try:
                storage.delete(default_bucket_for(_STORAGE_BACKEND), old_backup.storage_key)
            except Exception:
                # A storage-delete failure (e.g. the object was already
                # gone) must not block deleting the DB row itself — this
                # sweep's job is reclaiming disk, and a row pointing at
                # an already-missing object is exactly the kind of
                # orphan Phase 10.5's own sweep exists to catch, not a
                # reason to leave a 30-day-stale row behind forever.
                logger.warning(
                    "backups.retention_delete_failed", backup_id=str(old_backup.id), exc_info=True
                )
        db.delete(old_backup)
    if expired:
        db.commit()
    return len(expired)


@celery_app.task(name="backups.run_pg_dump", bind=True, max_retries=0)
def run_pg_dump(self, backup_id: str) -> dict:
    """Retries are deliberately disabled (max_retries=0, unlike most
    other tasks in this codebase) — a failed pg_dump is a real signal an
    admin needs to see and investigate (storage full, DB unreachable,
    permissions issue), not something to silently retry into a
    partially-written dump. The nightly beat schedule will simply try
    again the next night."""
    backup_uuid = uuid.UUID(backup_id)

    with get_sync_db() as db:
        backup = db.get(Backup, backup_uuid)
        if backup is None:
            logger.error("backups.backup_not_found", backup_id=backup_id)
            return {"status": "failed", "reason": "backup not found"}
        backup.status = BackupStatus.RUNNING.value
        backup.celery_task_id = self.request.id
        # Captured immediately before pg_dump actually runs, in the same
        # transaction as the status update above — the closest this task
        # can get to "exactly what pg_dump is about to see" without a
        # true point-in-time snapshot isolation guarantee (pg_dump's own
        # internal transaction already provides that for the dump
        # contents themselves; this is a best-effort approximation for
        # the comparison metadata only, not a claim of perfect
        # consistency with the dump's exact internal snapshot).
        backup.row_counts = _capture_row_counts(db)
        db.commit()

    try:
        with tempfile.TemporaryDirectory(prefix="bodp_backup_") as tmp_dir:
            dump_path = Path(tmp_dir) / "backup.dump"
            _run_pg_dump(dump_path)
            size_bytes = dump_path.stat().st_size

            bucket = default_bucket_for(_STORAGE_BACKEND)
            storage = get_storage_backend(_STORAGE_BACKEND)
            storage.ensure_bucket(bucket)
            key = backup_key(backup_uuid)
            with open(dump_path, "rb") as f:
                storage.put(bucket, key, f, content_type="application/octet-stream")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        logger.error("backups.pg_dump_failed", backup_id=backup_id, stderr=stderr)
        with get_sync_db() as db:
            backup = db.get(Backup, backup_uuid)
            if backup is not None:
                backup.status = BackupStatus.FAILED.value
                backup.error_message = f"pg_dump failed: {stderr[:1900]}"
                backup.completed_at = datetime.now(UTC)
                db.commit()
        return {"status": "failed", "reason": "pg_dump failed"}
    except Exception as exc:
        logger.exception("backups.unexpected_failure", backup_id=backup_id)
        with get_sync_db() as db:
            backup = db.get(Backup, backup_uuid)
            if backup is not None:
                backup.status = BackupStatus.FAILED.value
                backup.error_message = f"Unexpected error: {exc}"
                backup.completed_at = datetime.now(UTC)
                db.commit()
        return {"status": "failed", "reason": str(exc)}

    with get_sync_db() as db:
        backup = db.get(Backup, backup_uuid)
        if backup is not None:
            backup.status = BackupStatus.SUCCESS.value
            backup.storage_key = key
            backup.size_bytes = size_bytes
            backup.completed_at = datetime.now(UTC)
            db.commit()

        storage = get_storage_backend(_STORAGE_BACKEND)
        swept = _sweep_expired_backups(db, storage)

    logger.info("backups.completed", backup_id=backup_id, size_bytes=size_bytes, swept_expired=swept)
    return {"status": "complete", "size_bytes": size_bytes}


@celery_app.task(name="backups.run_scheduled_backup")
def run_scheduled_backup() -> dict:
    """Celery beat's own entry point (see celery_app.py's beat_schedule)
    — creates its own Backup row first, mirroring admin_stats.capture_
    daily_snapshot's shape (a scheduled task that needs no external
    caller to have already set up its own row), then runs the exact same
    run_pg_dump logic the on-demand "Run Backup Now" endpoint dispatches
    — one real code path for the actual dump work, not two, so the
    scheduled and on-demand paths can never silently drift apart."""
    with get_sync_db() as db:
        backup = Backup(status=BackupStatus.RUNNING.value)
        db.add(backup)
        db.commit()
        db.refresh(backup)
        backup_id = str(backup.id)

    return run_pg_dump(backup_id)
