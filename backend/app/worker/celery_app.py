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
)

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
}
