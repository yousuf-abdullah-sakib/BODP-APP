from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import UUIDPKMixin


class VizJobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class VisualizationJob(UUIDPKMixin, Base):
    """Job row for heavy visualization computations dispatched to Celery
    (Master Plan §3 Phase 7 task 2) — mirrors SubsetExtraction's shape
    (status/celery_task_id/error_message/timestamps), but public/
    unauthenticated (no user_id or grant_id FK) since /visualize/* has no
    auth gate. `job_type` is a plain string rather than an FK to a second
    table so future heavy viz jobs (e.g. a large comparison query) can
    reuse this same table without a schema change."""

    __tablename__ = "visualization_jobs"

    job_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VizJobStatus.QUEUED.value, index=True
    )
    result: Mapped[dict | None] = mapped_column(JSONB)
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
