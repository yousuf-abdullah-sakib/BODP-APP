from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "bodp",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    # Task modules are added phase by phase (Phase 2: file ingestion,
    # Phase 4: notifications, Phase 5: subset extraction, Phase 7: viz jobs).
    include=[
        "app.worker.tasks.ingestion",
        "app.worker.tasks.notifications",
        "app.worker.tasks.extraction",
        "app.worker.tasks.visualize",
        "app.worker.tasks.admin_stats",
        "app.worker.tasks.reports",
        "app.worker.tasks.bulk_import",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # Phase 2: dataset-file ingestion runs on its own queue, consumed by a
    # dedicated celery-worker-ingestion service (docker-compose.yml) with
    # concurrency=1 — a large NetCDF/GeoTIFF conversion job never blocks
    # (or gets blocked behind) lightweight tasks like notifications, and
    # running only one ingestion job at a time on that worker avoids
    # concurrent large jobs compounding memory pressure on the same
    # process. Every other task module stays on the default queue,
    # consumed by the existing celery-worker service.
    task_routes={
        "ingestion.process_dataset_file": {"queue": "ingestion"},
        # Bulk import's transfer step does the same kind of large-file I/O
        # as ingestion itself (streaming a potentially TB-scale file) —
        # routed to the same dedicated worker/queue so it never blocks (or
        # gets blocked behind) lightweight tasks either, and never
        # compounds memory pressure by running concurrently with an
        # ingestion job on a different worker.
        "bulk_import.run": {"queue": "ingestion"},
    },
)


def ingestion_soft_time_limit_seconds(size_bytes: int | None) -> int:
    """Computes a per-dispatch soft time limit for an ingestion task from
    the actual file's size, rather than one fixed global timeout that
    can't fit both a 1MB CSV and a 500GB NetCDF — see
    Settings.INGESTION_SOFT_TIME_LIMIT_BASE_SECONDS/_PER_GB's docstring in
    app/core/config.py for the reasoning behind the specific numbers, and
    _MAX_SECONDS for the optional (default: unlimited) ceiling.
    A soft (not hard) limit: raises a catchable SoftTimeLimitExceeded
    inside the task rather than forcibly killing the worker process,
    matching this task's existing pattern of handling every failure mode
    as a normal exception (see process_dataset_file's except clauses)."""
    size_gb = (size_bytes or 0) / (1024 * 1024 * 1024)
    computed = settings.INGESTION_SOFT_TIME_LIMIT_BASE_SECONDS + round(
        size_gb * settings.INGESTION_SOFT_TIME_LIMIT_SECONDS_PER_GB
    )
    max_seconds = settings.INGESTION_SOFT_TIME_LIMIT_MAX_SECONDS
    return computed if max_seconds is None else min(computed, max_seconds)


def ingestion_hard_time_limit_seconds(size_bytes: int | None) -> int:
    """Worker-level SIGKILL failsafe, always set above the soft limit —
    see Settings.INGESTION_HARD_TIME_LIMIT_GRACE_SECONDS's docstring for
    why this exists and why it's not itself the timeout ingestion.py's
    business logic reacts to."""
    return ingestion_soft_time_limit_seconds(size_bytes) + settings.INGESTION_HARD_TIME_LIMIT_GRACE_SECONDS

# Master Plan §3 Phase 4 task 9 — daily scheduled check for grants expiring
# soon, run by the celery-beat service (docker-compose.yml).
celery_app.conf.beat_schedule = {
    "check-expiring-grants-daily": {
        "task": "notifications.check_expiring_grants",
        "schedule": crontab(hour=6, minute=0),
    },
    # Master Plan §3 Phase 6 task 3 — revoke grants once a user's 30-day
    # account-deletion grace period elapses.
    "process-pending-deletions-daily": {
        "task": "notifications.process_pending_deletions",
        "schedule": crontab(hour=6, minute=30),
    },
    # Master Plan §3 Phase 8 task 7 — daily platform-stats snapshot backing
    # the admin Overview dashboard's real trend arrows/sparklines. Runs
    # right after the existing daily pair, same admin-jobs window.
    "capture-daily-stats-snapshot": {
        "task": "admin_stats.capture_daily_snapshot",
        "schedule": crontab(hour=6, minute=45),
    },
}
