import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class UserRoleEnum(StrEnum):
    """Coarse role — always present, checked on nearly every endpoint."""

    USER = "user"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


# The 8 fine-grained permissions from the admin "Roles & Permissions" screen.
# Source of truth for `Role.permissions` array contents — enforced server-side
# by app.core.permissions, not just used to render checkboxes in the UI.
PERMISSION_LIST = [
    "Approve Requests",
    "Manage Users",
    "Edit Datasets",
    "Delete Datasets",
    "Publish Content",
    "Manage Roles",
    "View Analytics",
    "Manage Backups",
    # Phase 9 — content/reporting admin surfaces.
    "Manage CMS",
    "Manage Blog",
    "Manage Media",
    "View Reports",
    "View Audit Log",
    # Contact form submissions + support tickets admin surfaces.
    "Manage Support",
    # Phase 3 (large-dataset ingestion roadmap) — admin schema review /
    # variable role assignment, distinct from routine "Edit Datasets" so it
    # can be delegated independently.
    "Review Datasets",
]


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    institution: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=UserRoleEnum.USER.value)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=UserStatus.ACTIVE.value)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    avatar_key: Mapped[str | None] = mapped_column(String(512))
    bio: Mapped[str | None] = mapped_column(Text)
    research_area: Mapped[str | None] = mapped_column(String(255))
    datasets_granted: Mapped[int] = mapped_column(default=0, nullable=False)
    # Set when the user requests account deletion (Master Plan §3 Phase 6
    # task 3 — soft delete with a 30-day grace period). The account stays
    # fully functional while this is set; a daily Celery beat task revokes
    # the user's active grants once 30 days have elapsed. Deletion/
    # anonymization of the row itself is intentionally out of scope here.
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user_roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == UserRoleEnum.ADMIN.value

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value


class Role(UUIDPKMixin, TimestampMixin, Base):
    """Fine-grained permission template (e.g. 'Data Manager', 'Reviewer')."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)

    user_roles: Mapped[list["UserRole"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class UserRole(UUIDPKMixin, Base):
    """A user's assignment to a fine-grained Role (in addition to their coarse role)."""

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="user_roles")
    role: Mapped["Role"] = relationship(back_populates="user_roles")

    __table_args__ = ()


class UserPreferences(Base):
    """1:1 with User — explicit typed columns (not a JSONB blob), matching
    SiteSettings' style for this kind of small config set (Master Plan §3
    Phase 6 task 4). Theme is deliberately NOT here — it stays client-only
    per Master Plan §0, already implemented via localStorage."""

    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    notify_request_status: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_new_dataset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_weekly_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_security_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_newsletter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    date_format: Mapped[str] = mapped_column(String(10), nullable=False, default="iso")
    coordinate_format: Mapped[str] = mapped_column(String(10), nullable=False, default="dd")
    compact_table_rows: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
