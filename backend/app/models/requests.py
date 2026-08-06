import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.catalog import Dataset
    from app.models.user import User


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GrantStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ExtractionStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class DatasetRequest(UUIDPKMixin, Base):
    __tablename__ = "dataset_requests"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    # Captures the user's active catalog-detail filter state at request time:
    # category/parameter/source/dateFrom/dateTo/spatial bounds (see Master Plan §1).
    search_criteria: Mapped[dict | None] = mapped_column(JSONB)
    supporting_document_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_files.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RequestStatus.PENDING.value, index=True
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_note: Mapped[str | None] = mapped_column(Text)

    dataset: Mapped["Dataset"] = relationship()
    user: Mapped["User"] = relationship(foreign_keys="DatasetRequest.user_id")
    grants: Mapped[list["AccessGrant"]] = relationship(back_populates="request")


class AccessGrant(UUIDPKMixin, Base):
    __tablename__ = "access_grants"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_requests.id", ondelete="SET NULL")
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=GrantStatus.ACTIVE.value, index=True
    )
    # Admin-narrowed (or original) scope bounding what may be extracted/downloaded.
    scope: Mapped[dict | None] = mapped_column(JSONB)

    dataset: Mapped["Dataset"] = relationship()
    user: Mapped["User"] = relationship(foreign_keys="AccessGrant.user_id")
    request: Mapped["DatasetRequest | None"] = relationship(back_populates="grants")
    extractions: Mapped[list["SubsetExtraction"]] = relationship(back_populates="grant")


class SubsetExtraction(UUIDPKMixin, Base):
    __tablename__ = "subset_extractions"

    grant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("access_grants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_scope: Mapped[dict | None] = mapped_column(JSONB)
    format: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ExtractionStatus.QUEUED.value, index=True
    )
    output_storage_backend: Mapped[str | None] = mapped_column(String(20))
    output_bucket: Mapped[str | None] = mapped_column(String(255))
    output_file_key: Mapped[str | None] = mapped_column(String(1024))
    output_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    grant: Mapped["AccessGrant"] = relationship(back_populates="extractions")
    download_logs: Mapped[list["DownloadLog"]] = relationship(back_populates="subset_extraction")


class DownloadLog(UUIDPKMixin, Base):
    __tablename__ = "download_logs"

    grant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("access_grants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subset_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subset_extractions.id", ondelete="SET NULL")
    )
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ip_address: Mapped[str | None] = mapped_column(String(64))

    subset_extraction: Mapped["SubsetExtraction | None"] = relationship(
        back_populates="download_logs"
    )
