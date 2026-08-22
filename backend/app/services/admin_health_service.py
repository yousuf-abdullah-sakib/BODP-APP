"""Detailed system health for the dedicated SystemHealthSection admin
page (Master Plan §3 Phase 10 task 5) — deliberately separate from
admin_overview_service._check_system_health, which stays exactly as-is
(a fast DB/Redis/storage check that loads on every admin dashboard
visit). This function affords a slower, deeper check: Celery worker
liveness via a real inspect() round trip, disk usage, and per-backend
storage checks — the kind of thing a dedicated page can afford but a
widget loaded on every navigation shouldn't pay for."""

import asyncio
import shutil
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.config import settings
from app.core.database import engine
from app.models.admin import Backup
from app.schemas.admin_health import (
    CeleryHealth,
    CeleryQueueHealth,
    DatabaseHealth,
    DetailedHealthResponse,
    DiskHealth,
    LastBackupSummary,
    RedisHealth,
    StorageBackendHealth,
)
from app.services.storage.registry import get_storage_backend
from app.worker.celery_app import celery_app

# Real queues configured in celery_app.py's task_routes — every task not
# explicitly routed lands on "celery" (Celery's own default queue name),
# not listed in task_routes itself but real and always present.
_KNOWN_QUEUES = ["celery", "ingestion"]

_CELERY_INSPECT_TIMEOUT_SECONDS = 3.0


async def _check_database(db: AsyncSession) -> DatabaseHealth:
    try:
        await db.execute(select(1))
    except Exception as exc:
        return DatabaseHealth(healthy=False, pool_checked_out=None, pool_size=None, error=str(exc))

    pool = engine.pool
    return DatabaseHealth(
        healthy=True,
        pool_checked_out=pool.checkedout(),
        pool_size=pool.size(),
    )


async def _check_redis() -> RedisHealth:
    try:
        client = get_redis()
        await client.ping()
        info = await client.info("memory")
        return RedisHealth(healthy=True, used_memory_bytes=info.get("used_memory"))
    except Exception as exc:
        return RedisHealth(healthy=False, used_memory_bytes=None, error=str(exc))


def _configured_storage_backends() -> list[str]:
    backends = ["vps_minio"]
    if settings.STORAGE_CLOUD_ENDPOINT_URL and settings.STORAGE_CLOUD_ACCESS_KEY and settings.STORAGE_CLOUD_SECRET_KEY:
        backends.append("cloud")
    return backends


# Phase 10.4 finding, confirmed live: S3CompatibleBackend's shared boto3
# client has no connect_timeout/read_timeout configured, so it falls
# back to boto3's own ~60s defaults multiplied by STORAGE_MAX_RETRIES'
# "standard" retry mode — a genuinely unreachable endpoint (confirmed by
# stopping the dev MinIO container) took 24 REAL seconds to report
# unhealthy, not a crash but a bad experience for a health probe that
# should fail fast. Rather than change the shared client's global
# timeout (used by every real upload/download in the app, where a
# longer timeout is often the RIGHT choice for a large, slow-but-alive
# transfer — changing it globally risks turning a legitimate slow
# upload into a spurious failure), this wraps just the health check's
# own call with a short, health-check-specific deadline.
_STORAGE_CHECK_TIMEOUT_SECONDS = 3.0


async def _check_storage() -> list[StorageBackendHealth]:
    results = []
    for name in _configured_storage_backends():
        try:
            backend = get_storage_backend(name)
            bucket = settings.STORAGE_VPS_BUCKET if name == "vps_minio" else settings.STORAGE_CLOUD_BUCKET
            await asyncio.wait_for(
                asyncio.to_thread(backend.ensure_bucket, bucket), timeout=_STORAGE_CHECK_TIMEOUT_SECONDS
            )
            results.append(StorageBackendHealth(name=name, healthy=True))
        except TimeoutError:
            results.append(
                StorageBackendHealth(
                    name=name, healthy=False,
                    error=f"Timed out after {_STORAGE_CHECK_TIMEOUT_SECONDS}s (endpoint unreachable or very slow)",
                )
            )
        except Exception as exc:
            results.append(StorageBackendHealth(name=name, healthy=False, error=str(exc)))
    return results


def _inspect_active_queues() -> dict | None:
    inspect = celery_app.control.inspect(timeout=_CELERY_INSPECT_TIMEOUT_SECONDS)
    return inspect.active_queues()


async def _check_celery() -> CeleryHealth:
    try:
        # celery_app.control.inspect() is a real, blocking network round
        # trip under the hood (not awaitable) — run via to_thread so it
        # doesn't block this async endpoint's event loop for up to
        # _CELERY_INSPECT_TIMEOUT_SECONDS on every health check, which
        # would otherwise stall every OTHER concurrent request this
        # worker process is handling, not just this one.
        active_queues = await asyncio.to_thread(_inspect_active_queues)
    except Exception as exc:
        return CeleryHealth(healthy=False, queues=[], error=str(exc))

    if not active_queues:
        # No workers responded within the timeout at all — every known
        # queue is unhealthy, not just unlisted, since "no response" and
        # "responded but consuming nothing" are genuinely different
        # failure modes worth distinguishing in the error message but
        # not in the per-queue healthy/unhealthy signal itself.
        return CeleryHealth(
            healthy=False,
            queues=[CeleryQueueHealth(queue=q, healthy=False, worker_count=0) for q in _KNOWN_QUEUES],
            error="No Celery workers responded within the inspect timeout",
        )

    consumed_queues: dict[str, int] = {}
    for worker_queues in active_queues.values():
        for q in worker_queues:
            name = q.get("name")
            if name:
                consumed_queues[name] = consumed_queues.get(name, 0) + 1

    queue_health = [
        CeleryQueueHealth(queue=q, healthy=consumed_queues.get(q, 0) > 0, worker_count=consumed_queues.get(q, 0))
        for q in _KNOWN_QUEUES
    ]
    return CeleryHealth(healthy=all(q.healthy for q in queue_health), queues=queue_health)


def _check_disk() -> DiskHealth:
    usage = shutil.disk_usage(settings.DISK_USAGE_CHECK_PATH)
    return DiskHealth(
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        percent_used=round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
    )


async def _last_backup(db: AsyncSession) -> LastBackupSummary | None:
    result = await db.execute(select(Backup).order_by(Backup.started_at.desc()).limit(1))
    backup = result.scalar_one_or_none()
    if backup is None:
        return None
    return LastBackupSummary(
        id=str(backup.id), status=backup.status, started_at=backup.started_at, completed_at=backup.completed_at
    )


async def get_detailed_health(db: AsyncSession) -> DetailedHealthResponse:
    return DetailedHealthResponse(
        checked_at=datetime.now(UTC),
        database=await _check_database(db),
        redis=await _check_redis(),
        storage=await _check_storage(),
        celery=await _check_celery(),
        disk=_check_disk(),
        last_backup=await _last_backup(db),
    )
