import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ARRAY, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import UUIDPKMixin


class BlogPostStatus(StrEnum):
    PUBLISHED = "published"
    DRAFT = "draft"


class BackupStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class BlogPost(UUIDPKMixin, Base):
    """Unified blog shape — replaces the prototype's two disconnected BlogPost/BlogPostEntry
    types. Both the admin CRUD screen and the public /blog page read this same table."""

    __tablename__ = "blog_posts"

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    tag_key: Mapped[str | None] = mapped_column(String(50))
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    author_name: Mapped[str | None] = mapped_column(String(255))
    excerpt: Mapped[str | None] = mapped_column(Text)
    content_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=BlogPostStatus.DRAFT.value)
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    featured_image_key: Mapped[str | None] = mapped_column(String(1024))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=datetime.utcnow
    )


class MediaFile(UUIDPKMixin, Base):
    __tablename__ = "media_files"

    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CmsBlock(Base):
    """Key/value content block, editable per page. Public Home/About/Contact/Footer pages
    read from this table — closing the prototype's Footer/CMS disconnect (Master Plan §1)."""

    __tablename__ = "cms_blocks"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    page: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(255))
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=datetime.utcnow
    )


class AboutTeamMember(UUIDPKMixin, Base):
    __tablename__ = "about_team_members"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(255))
    bio: Mapped[str | None] = mapped_column(Text)
    photo_key: Mapped[str | None] = mapped_column(String(1024))
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AdminTeamMember(UUIDPKMixin, Base):
    """Internal 'who has admin access' roster — distinct from Role/Permission templates."""

    __tablename__ = "admin_team_members"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role_label: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Report(UUIDPKMixin, Base):
    __tablename__ = "reports"

    type: Mapped[str] = mapped_column(String(100), nullable=False)
    date_range: Mapped[str | None] = mapped_column(String(100))
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    output_storage_key: Mapped[str | None] = mapped_column(String(1024))
    celery_task_id: Mapped[str | None] = mapped_column(String(255))


class Backup(UUIDPKMixin, Base):
    __tablename__ = "backups"

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(1024))


class SiteSettings(Base):
    """Single-row config table."""

    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    site_name: Mapped[str] = mapped_column(String(255), nullable=False, default="BODP")
    contact_email: Mapped[str | None] = mapped_column(String(320))
    data_access_email: Mapped[str | None] = mapped_column(String(320))
    max_upload_size_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    session_lifetime_min: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    notify_new_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_new_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_expiring_dataset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BoundaryShapefile(UUIDPKMixin, Base):
    """Backs the shared admin-uploader / public-map-clip-boundary feature."""

    __tablename__ = "boundary_shapefiles"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    geojson: Mapped[dict] = mapped_column(JSONB, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
