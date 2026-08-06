import uuid
from datetime import date
from enum import StrEnum

from geoalchemy2 import Geometry
from sqlalchemy import ARRAY, BigInteger, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class DatasetStatus(StrEnum):
    PUBLISHED = "published"
    DRAFT = "draft"
    ARCHIVED = "archived"


class StorageBackend(StrEnum):
    VPS_MINIO = "vps_minio"
    CLOUD = "cloud"


class QualityFlag(StrEnum):
    NORMAL = "normal"
    CAUTION = "caution"
    ALERT = "alert"


class DatasetCategory(UUIDPKMixin, TimestampMixin, Base):
    """Admin-managed taxonomy — real data, not a hardcoded enum (see Master Plan §1)."""

    __tablename__ = "dataset_categories"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color_tag: Mapped[str] = mapped_column(String(50), nullable=False, default="cat-default")

    datasets: Mapped[list["Dataset"]] = relationship(back_populates="category")


class Station(UUIDPKMixin, Base):
    __tablename__ = "stations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    lat: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    lon: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    depth_m: Mapped[float | None] = mapped_column(Numeric(8, 2))


class Dataset(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "datasets"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_categories.id", ondelete="SET NULL")
    )
    location: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(255))
    platforms: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    parameters: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    resolution: Mapped[str | None] = mapped_column(String(100))
    license: Mapped[str | None] = mapped_column(String(255))
    processing_levels: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    formats: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DatasetStatus.DRAFT.value, index=True
    )
    spatial_extent: Mapped[str | None] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=True)
    )
    temporal_start: Mapped[date | None] = mapped_column(Date)
    temporal_end: Mapped[date | None] = mapped_column(Date)
    record_count: Mapped[int] = mapped_column(default=0, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    category: Mapped["DatasetCategory | None"] = relationship(back_populates="datasets")
    files: Mapped[list["DatasetFile"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )
    records: Mapped[list["DatasetRecord"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class DatasetFile(UUIDPKMixin, Base):
    """A single stored object belonging to a dataset — may live on either storage tier."""

    __tablename__ = "dataset_files"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_format: Mapped[str | None] = mapped_column(String(50))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(128))
    spatial_extent: Mapped[str | None] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=True)
    )
    temporal_start: Mapped[date | None] = mapped_column(Date)
    temporal_end: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[date] = mapped_column(Date, server_default=None, nullable=True)
    # Auto-detected file metadata (variables, dimensions, columns) from Phase 2 parsers.
    file_metadata: Mapped[dict | None] = mapped_column(JSONB)

    dataset: Mapped["Dataset"] = relationship(back_populates="files")


class DatasetRecord(UUIDPKMixin, Base):
    """Normalized observation row — used for catalog preview/filtering and light viz queries."""

    __tablename__ = "dataset_records"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    time: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    lon: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    depth_m: Mapped[float | None] = mapped_column(Numeric(8, 2))
    location: Mapped[str | None] = mapped_column(String(255))
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL")
    )
    parameter: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50))
    quality_flag: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QualityFlag.NORMAL.value
    )
    processing_level: Mapped[str | None] = mapped_column(String(50))
    format: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str | None] = mapped_column(String(255))
    geom: Mapped[str | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True)
    )

    dataset: Mapped["Dataset"] = relationship(back_populates="records")
