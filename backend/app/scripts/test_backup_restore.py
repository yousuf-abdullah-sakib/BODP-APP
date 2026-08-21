"""Restores a real backup dump into a throwaway database on the isolated
dev/test Postgres instance, then runs sanity queries to confirm the
restored data matches what was actually backed up — this is what
"test an actual restore, not just log an audit entry" (Master Plan §3
Phase 10 task 4's own quality check) means in practice.

Never touches the real dev-stack or production database — connects to
the SAME isolated Postgres container docs/TESTING.md already documents
for pytest (disposable by design), creates a uniquely-named throwaway
database there, and drops it in a finally block (even on failure) so
this is safe to re-run repeatedly.

Usage:
    python -m app.scripts.test_backup_restore <backup_id>

Requires TEST_DATABASE_URL_SYNC pointing at the isolated Postgres
instance's *server* (not a specific database — this script creates its
own), e.g.:
    postgresql+psycopg://bodp:bodp@localhost:55434/postgres          (bare metal)
    postgresql+psycopg://bodp:bodp@host.docker.internal:55434/postgres  (container)
"""

import argparse
import asyncio
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.admin import Backup
from app.services.storage.registry import default_bucket_for, get_storage_backend

_STORAGE_BACKEND = "vps_minio"

# Cheap, representative sanity check — not exhaustive. A handful of core
# tables whose row counts should match exactly between the live source
# database (at dump time) and the restored copy.
_SANITY_TABLES = ["users", "datasets", "dataset_files"]


def _server_dsn_parts(test_database_url_sync: str) -> tuple[str, int, str, str]:
    parsed = urlparse(test_database_url_sync)
    return (
        parsed.hostname or "localhost",
        parsed.port or 5432,
        parsed.username or "bodp",
        parsed.password or "bodp",
    )


async def _fetch_backup(backup_id: uuid.UUID) -> tuple[str, dict[str, int]]:
    """Dedicated engine created and disposed entirely within this one
    asyncio.run() call — never the app's own global AsyncSessionLocal,
    whose engine is permanently bound to whichever event loop first used
    it (this script's own asyncio.run() creates a fresh one, causing
    "Future attached to a different loop" if the global engine were
    reused here — the exact same class of bug worker/tasks/snapshots.py's
    own docstring already documents and works around identically).

    Returns the backup's storage_key and its row_counts CAPTURED AT DUMP
    TIME (worker/tasks/backups.py._capture_row_counts) — not a fresh live
    query against the current database, which would be inherently racy
    against any write that happens between the dump and this
    verification run (confirmed as a real false-positive in practice: an
    earlier version of this script compared against a live query and
    reported a mismatch caused entirely by a test user created after the
    dump, not any real restore defect)."""
    engine = create_async_engine(str(settings.DATABASE_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with session_factory() as db:
            backup = await db.get(Backup, backup_id)
            if backup is None:
                raise ValueError(f"No backup found with id {backup_id}")
            if not backup.storage_key:
                raise ValueError(f"Backup {backup_id} has no storage_key — it never completed successfully")
            if not backup.row_counts:
                raise ValueError(
                    f"Backup {backup_id} has no row_counts recorded — it predates this column "
                    "and cannot be verified by this script (only fresh backups can)."
                )
            return backup.storage_key, backup.row_counts
    finally:
        await engine.dispose()


def _restored_row_counts(host: str, port: int, user: str, password: str, dbname: str) -> dict[str, int]:
    counts = {}
    with psycopg.connect(host=host, port=port, user=user, password=password, dbname=dbname) as conn:
        with conn.cursor() as cur:
            for table in _SANITY_TABLES:
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - table names are from a fixed internal list, never user input
                counts[table] = cur.fetchone()[0]
    return counts


def restore_and_verify(backup_id: uuid.UUID) -> dict:
    test_db_url = os.environ.get("TEST_DATABASE_URL_SYNC")
    if not test_db_url:
        raise ValueError(
            "TEST_DATABASE_URL_SYNC is not set — point it at the isolated "
            "dev/test Postgres instance's server (see this script's own "
            "docstring for the exact connection string)."
        )
    host, port, user, password = _server_dsn_parts(test_db_url)

    storage_key, dump_time_counts = asyncio.run(_fetch_backup(backup_id))

    storage = get_storage_backend(_STORAGE_BACKEND)
    bucket = default_bucket_for(_STORAGE_BACKEND)

    throwaway_db = f"bodp_restore_test_{uuid.uuid4().hex[:12]}"
    env = {**os.environ, "PGPASSWORD": password}

    with tempfile.TemporaryDirectory(prefix="bodp_restore_test_") as tmp_dir:
        dump_path = Path(tmp_dir) / "backup.dump"
        body = storage.get(bucket, storage_key)
        with open(dump_path, "wb") as f:
            while chunk := body.read(1024 * 1024):
                f.write(chunk)

        subprocess.run(
            ["createdb", "--host", host, "--port", str(port), "--username", user, throwaway_db],
            env=env, capture_output=True, check=True,
        )
        try:
            subprocess.run(
                [
                    "pg_restore",
                    "--host", host,
                    "--port", str(port),
                    "--username", user,
                    "--dbname", throwaway_db,
                    "--no-owner",
                    "--no-privileges",
                    str(dump_path),
                ],
                env=env, capture_output=True, check=True,
            )
            restored_counts = _restored_row_counts(host, port, user, password, throwaway_db)
        finally:
            subprocess.run(
                [
                    "dropdb", "--host", host, "--port", str(port), "--username", user,
                    "--if-exists", throwaway_db,
                ],
                env=env, capture_output=True, check=True,
            )

    mismatches = {
        table: (dump_time_counts[table], restored_counts[table])
        for table in _SANITY_TABLES
        if dump_time_counts.get(table) != restored_counts[table]
    }

    return {
        "backup_id": str(backup_id),
        "storage_key": storage_key,
        "throwaway_db_dropped": True,
        "dump_time_row_counts": dump_time_counts,
        "restored_row_counts": restored_counts,
        "mismatches": mismatches,
        "restore_verified": not mismatches,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backup_id", type=str)
    args = parser.parse_args()

    result = restore_and_verify(uuid.UUID(args.backup_id))
    for key, value in result.items():
        print(f"{key}: {value}")
    if not result["restore_verified"]:
        raise SystemExit(1)
