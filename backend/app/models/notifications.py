import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class NotificationType(StrEnum):
    SUCCESS = "success"
    WARNING = "warning"
    INFO = "info"
    DANGER = "danger"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TicketStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"


class ContactSubmissionStatus(StrEnum):
    NEW = "new"
    REPLIED = "replied"


class Notification(UUIDPKMixin, Base):
    """Unified notification table — serves both user-facing and admin-facing lists,
    scoped by user_id (a None user_id with role scoping could be added later for
    broadcast-to-all-admins; Phase 4 dispatch targets specific admin user_ids)."""

    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False, default=NotificationType.INFO.value)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class SupportTicket(UUIDPKMixin, Base):
    __tablename__ = "support_tickets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user: Mapped["User"] = relationship(foreign_keys="SupportTicket.user_id")
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default=TicketPriority.LOW.value)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TicketStatus.OPEN.value)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Single admin reply per ticket — matches the Master Plan's "admin
    # replies, user sees it in their dashboard" scope; no threaded
    # back-and-forth. reply_* stay null until an admin answers.
    reply_message: Mapped[str | None] = mapped_column(Text)
    replied_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    replied_by: Mapped["User | None"] = relationship(foreign_keys="SupportTicket.replied_by_id")
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContactSubmission(UUIDPKMixin, TimestampMixin, Base):
    """Public /contact page form submissions. Replies are sent by email to
    the submitter's provided address (not an in-app message — the
    submitter isn't necessarily a registered user), unlike SupportTicket's
    in-dashboard reply."""

    __tablename__ = "contact_submissions"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    organization: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ContactSubmissionStatus.NEW.value
    )
    reply_message: Mapped[str | None] = mapped_column(Text)
    replied_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    replied_by: Mapped["User | None"] = relationship(foreign_keys="ContactSubmission.replied_by_id")
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
