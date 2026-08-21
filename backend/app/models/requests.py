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


class RequestSupportingDocument(UUIDPKMixin, TimestampMixin, Base):
    """A user-supplied attachment (PDF/DOC/DOCX) justifying a DatasetRequest.

    Deliberately separate from DatasetFile (the scientific-data table):
    this table has none of DatasetFile's dataset_id/storage_kind/
    spatial_extent/temporal fields, only what an uploaded attachment
    actually needs.
    """

    __tablename__ = "request_supporting_documents"

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    request: Mapped["DatasetRequest"] = relationship(foreign_keys="RequestSupportingDocument.request_id")


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
    # category/parameters/source/dateFrom/dateTo/spatial bounds (see Master
    # Plan §1). Immutable after creation — an admin's edits during review
    # go into admin_modified_search_criteria below, never here, so the
    # original request a user submitted can always be reconstructed
    # exactly, independent of anything an admin later changed.
    search_criteria: Mapped[dict | None] = mapped_column(JSONB)
    # Admin's edited filter configuration, saved via the explicit "Save
    # Changes" action (PATCH /admin/requests/{id}/modify) — separate from
    # search_criteria (the original) and from AccessGrant.scope (the
    # final approved configuration, set at approval time from this field
    # if present, else from search_criteria). Null until an admin actually
    # modifies and saves; a request can be approved with this still null,
    # in which case the original search_criteria becomes the grant's scope
    # unchanged, exactly as before this field existed.
    admin_modified_search_criteria: Mapped[dict | None] = mapped_column(JSONB)
    # Supporting Document feature: FK originally targeted dataset_files.id
    # (the scientific-data table) before this column was ever actually
    # written to by any code path — confirmed via full-codebase trace
    # that it stayed permanently NULL (see the "Supporting Document
    # Missing from Admin Review" investigation). Repointed to the new,
    # correctly-shaped RequestSupportingDocument table below rather than
    # reusing DatasetFile, which requires dataset_id/storage_kind/
    # spatial_extent/temporal_start-end — none of which describe a user's
    # PDF attachment. The one-nullable-FK RELATIONSHIP concept on
    # DatasetRequest is preserved; only its target table changed.
    supporting_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "request_supporting_documents.id",
            ondelete="SET NULL",
            use_alter=True,
            name="dataset_requests_supporting_document_id_fkey",
        ),
    )
    # Coverage snapshot — computed ONCE via catalog_service.
    # get_matching_record_counts at request-creation time (requests_
    # service.create_request) and never recomputed afterward. Previously
    # the admin dashboard recomputed this live, from scratch, for every
    # request on every page load (confirmed via profiling: ~96% of that
    # endpoint's server time). Nullable because a request created before
    # this column existed has no snapshot to backfill from — shown as
    # "not available" rather than fabricated as 0.
    matching_record_count: Mapped[int | None] = mapped_column()
    dataset_total_record_count: Mapped[int | None] = mapped_column()
    matching_percent: Mapped[float | None] = mapped_column()
    # Dataset.version at the moment this snapshot was computed — lets the
    # admin dashboard detect staleness (dataset.version has since moved
    # on, meaning matching_record_count/dataset_total_record_count above
    # may no longer reflect the dataset's actual current contents)
    # without needing to recompute anything. Nullable for the same
    # pre-existing-row reason as the snapshot counts above.
    dataset_version: Mapped[int | None] = mapped_column()
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
    supporting_document: Mapped["RequestSupportingDocument | None"] = relationship(
        foreign_keys="DatasetRequest.supporting_document_id"
    )


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
