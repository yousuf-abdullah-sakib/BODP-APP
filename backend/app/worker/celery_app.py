from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "bodp",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    # Task modules are added phase by phase (Phase 2: file ingestion,
    # Phase 4: notifications, Phase 5: subset extraction, Phase 7: viz jobs).
    include=["app.worker.tasks.ingestion"],
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
