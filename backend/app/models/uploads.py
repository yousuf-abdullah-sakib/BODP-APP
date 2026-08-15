import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import UUIDPKMixin

if TYPE_CHECKING:
    from app.models.catalog import Dataset


class UploadStatus(StrEnum):
    # INITIATED: a multipart session has been created in storage but no
    # parts have been PUT yet (or completion hasn't been confirmed) — the
    # single-request small-file path never enters this state, it starts
    # directly at QUEUED once the whole file is already in storage.
    INITIATED = "initiated"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"
    # CANCELLED is deliberately distinct from FAILED — FAILED means
    # ingestion/validation rejected the file; CANCELLED means the
    # admin/user explicitly aborted before or during upload/processing.
    CANCELLED = "cancelled"


class QualityIssueType(StrEnum):
    MISSING_VALUES = "Missing Values"
    OUTLIERS = "Outliers"
    DUPLICATE_RECORDS = "Duplicate Records"
    SCHEMA_MISMATCH = "Schema Mismatch"


class QualityIssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class QualityIssueStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class Upload(UUIDPKMixin, Base):
    __tablename__ = "uploads"

    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE")
    )
    dataset_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_files.id", ondelete="SET NULL")
    )
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=UploadStatus.QUEUED.value)
    error_message: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(255))

    # --- Multipart upload session state (Phase 1) ---
    # All nullable: the existing single-request small-file path never
    # populates any of these — it uploads the whole file in one call and
    # only ever creates an Upload row after the object already exists in
    # storage, so it has no "session" to track.
    multipart_upload_id: Mapped[str | None] = mapped_column(String(255))
    storage_bucket: Mapped[str | None] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    total_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    total_parts: Mapped[int | None] = mapped_column(Integer)
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # --- Processing progress (Phase 1 plumbing; populated by ingestion in
    # Phase 2 once the pipeline is chunked and can report real sub-stages) ---
    progress_stage: Mapped[str | None] = mapped_column(String(50))
    progress_pct: Mapped[int | None] = mapped_column(Integer)


class QualityIssue(UUIDPKMixin, Base):
    __tablename__ = "quality_issues"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    issue_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QualityIssueStatus.OPEN.value
    )
    detail: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dataset: Mapped["Dataset"] = relationship()
