import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import UUIDPKMixin


class AuditActionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REVOKE = "revoke"
    USER = "user"
    DATASET = "dataset"
    CONTENT = "content"
    LOGIN = "login"


class AuditLogEntry(UUIDPKMixin, Base):
    __tablename__ = "audit_log"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_name: Mapped[str | None] = mapped_column(String(255))
    actor_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class FailedLogin(UUIDPKMixin, Base):
    __tablename__ = "failed_logins"

    email_attempted: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class ActiveSession(UUIDPKMixin, Base):
    """Real refresh-token session tracking, backing Security section session list/revoke."""

    __tablename__ = "active_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    device: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
