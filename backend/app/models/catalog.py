import uuid
from datetime import date, datetime
from enum import StrEnum

from geoalchemy2 import Geometry
from sqlalchemy import ARRAY, BigInteger, Date, DateTime, ForeignKey, Numeric, String, Text, func
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


class StorageKind(StrEnum):
    """How a DatasetFile's actual observation values are stored/queried
    (PLAN.md Phase 5 — Storage & Query Architecture). This is the field
    catalog_service.py/visualize_service.py/admin_qc_service.py branch on
    to route a query to the right backend, replacing the old fragile
    ".zarr.zip" processed_key suffix check.

    ROW_RECORDS: legacy — data lives in DatasetRecord SQL rows. Every
    DatasetFile ingested before Phase 5 effectively has this value
    (backfilled best-effort, never fabricated as certain — see the
    backfill_storage_kind.py script). New ingestion NEVER sets this.
    PARQUET: tabular data in a Parquet file under processed/, queried via
    DuckDB (tabular_query_service.py).
    CHUNKED_ARRAY: gridded data in a Zarr store under processed/, queried
    via xarray (gridded_query_service.py).
    RASTER: GeoTIFF/COG — queried directly by URL, never through a
    DatasetRecord-shaped filter/aggregate path at all (unchanged from
    before this phase).
    """

    ROW_RECORDS = "row_records"
    PARQUET = "parquet"
    CHUNKED_ARRAY = "chunked_array"
    RASTER = "raster"


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
    # Bumped by ingestion (worker/tasks/ingestion.py's _run_ingestion) on
    # every SUCCESSFUL, content-changing ingestion of a file into this
    # dataset — NOT gated on record_count actually changing (a genuinely
    # successful ingestion of a zero-content file, e.g. a header-only
    # CSV, must still bump this; record_count's own truthiness was
    # tried first and rejected as the gate for exactly that reason — see
    # _run_ingestion's inline comment for the full reasoning). A failed
    # or cancelled ingestion never reaches the code path that bumps this,
    # so it stays correctly frozen for those. Lets a DatasetRequest
    # snapshot (matching_record_count/dataset_total_record_count,
    # captured once at request-submission time — see DatasetRequest.
    # dataset_version below) be compared against the dataset's CURRENT
    # version to detect staleness: if dataset.version has moved on since
    # the request captured it, a real content-changing ingestion has
    # happened since, and the snapshot numbers may no longer reflect the
    # dataset's actual contents. Starts at 1 (not 0) so "version 1" means
    # "as first ingested", matching DatasetFile.version's own 1-based
    # convention.
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Orthogonal to Upload.status (Phase 1) — a dataset can be fully
    # ingested and still "pending schema review" (PLAN.md Phase 3). Null
    # until an admin with "Review Datasets" explicitly approves the
    # detected DatasetVariable rows; set/cleared together by
    # admin_dataset_schema_service, never touched by ingestion itself.
    schema_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Default-View Snapshot feature: which `version` (above) the currently
    # stored snapshot object (storage/keys.py's snapshot_key) was built
    # from. NULL means no snapshot has ever been generated. A read
    # endpoint trusts the stored snapshot only when this equals `version`
    # exactly — any mismatch means a content-changing ingestion has
    # happened since the snapshot was built, so it's treated as stale and
    # the caller falls back to the live query path unchanged. Set only by
    # worker/tasks/snapshots.py, only after the snapshot object has been
    # durably written to storage (never before — an in-flight write must
    # never be pointed to by a "fresh" version number).
    snapshot_version: Mapped[int | None] = mapped_column(nullable=True)

    category: Mapped["DatasetCategory | None"] = relationship(back_populates="datasets")
    files: Mapped[list["DatasetFile"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )
    records: Mapped[list["DatasetRecord"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )
    variables: Mapped[list["DatasetVariable"]] = relationship(
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
    # Phase 5: how this file's actual data is stored/queried (StorageKind).
    # Nullable — pre-Phase-5 files have no reliable provenance for this
    # from before the column existed; backfilled best-effort (never
    # fabricated as certain) by backfill_storage_kind.py. New ingestion
    # always sets this explicitly from the parser's ParsedFileMetadata.shape.
    storage_kind: Mapped[str | None] = mapped_column(String(20), index=True)

    dataset: Mapped["Dataset"] = relationship(back_populates="files")


class DatasetRecord(UUIDPKMixin, Base):
    """Normalized observation row — used for catalog preview/filtering and light viz queries."""

    __tablename__ = "dataset_records"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Provenance: which DatasetFile's ingestion wrote this row. Nullable —
    # rows written before this column existed have no reconstructable
    # provenance and are left NULL rather than fabricated. No ON DELETE
    # clause (defaults to NO ACTION/RESTRICT): deleting a DatasetFile while
    # DatasetRecords still reference it must fail loudly, not silently
    # cascade-delete millions of rows. Every code path that deletes a
    # DatasetFile explicitly deletes its DatasetRecords by this column
    # first (see ingestion.py's _cleanup_cancelled_ingestion,
    # dataset_multipart_upload_service.py, cleanup_orphaned_upload.py).
    # Also what makes a retry of the same file idempotent: ingestion
    # deletes any existing rows for this dataset_file_id before writing.
    dataset_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_files.id"), index=True
    )
    # Nullable (Phase 2): not every scientific dataset has every dimension —
    # a static spatial grid snapshot may have lat/lon with no time; a
    # non-spatial time series may have time with no lat/lon. Ingestion
    # (_write_dataset_records) writes whichever of these a given file
    # actually contains and leaves the rest null, rather than skipping the
    # whole file when one is absent (the pre-Phase-2 behavior, which is
    # exactly why Wave Data's timeless CSV never populated any records).
    # At least one of time / (lat & lon) / station_id must be present —
    # enforced in application code (_write_dataset_records), not a DB CHECK
    # constraint, so a clear rejection reason can be raised rather than a
    # generic constraint-violation error.
    time: Mapped[date | None] = mapped_column(Date, index=True)
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lon: Mapped[float | None] = mapped_column(Numeric(9, 6))
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


class VariableDataType(StrEnum):
    """Broad, format-agnostic classification of a detected column/variable —
    just enough for the Phase 4 filter UI to pick a sensible control
    (date range / numeric min-max / categorical dropdown / text), not a
    full type system."""

    NUMERIC = "numeric"
    TEMPORAL = "temporal"
    CATEGORICAL = "categorical"
    TEXT = "text"


class VariableRole(StrEnum):
    """What an admin has approved a detected variable to be used for
    (Phase 3's review workflow). A DatasetVariable row created by
    ingestion (Phase 2) always starts with role=None — unreviewed, not
    usable for filtering or visualization until an admin sets one of
    these. A single variable may hold more than one role (e.g. both a
    Data Variable and a Visualization Variable), hence roles is a list
    column below rather than one enum value."""

    DIMENSION = "dimension"
    DATA_VARIABLE = "data_variable"
    VISUALIZATION_VARIABLE = "visualization_variable"


# Cap on how many distinct values get sampled/stored per categorical
# variable — protects against an unbounded live SELECT DISTINCT at
# TB-scale row counts (see DatasetVariable.distinct_values below). Existing
# purely to make later dropdown-style filter UIs (Phase 4) usable, not to
# be an exhaustive enumeration.
_MAX_SAMPLED_DISTINCT_VALUES = 50


class DatasetVariable(UUIDPKMixin, Base):
    """Schema registry row — one per variable/column/dimension a parser
    detected in a dataset's file(s) (Master Plan Phase 2 extension: PLAN.md
    Phase 2, 'Automatic variable detection, persisted via a new schema
    registry table'). Written by ingestion at detection time for every
    format (CSV/NetCDF/.mat/GeoTIFF); `roles` stays empty/unset until an
    admin reviews it (PLAN.md Phase 3) — this table's existence and
    population is entirely Phase 2's responsibility, the review/approval
    workflow on top of it is not.

    Unique on (dataset_id, name): re-ingesting an additional file for the
    same dataset that happens to redetect the same variable name updates
    the existing row's stats (widening min/max, refreshing distinct_values)
    rather than creating a duplicate — mirrors how Dataset.parameters
    already merges across multiple files for the same dataset."""

    __tablename__ = "dataset_variables"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False, default=VariableDataType.TEXT.value)
    unit: Mapped[str | None] = mapped_column(String(50))
    # Whether this variable is a coordinate/dimension (time, lat, lon,
    # depth, station) as opposed to an observed data variable — mirrors
    # each parser's own lat_col/lon_col/time_col detection plus, for
    # raster bands and non-coordinate .mat/NetCDF variables, "false".
    is_dimension: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Unset (empty array) until an admin reviews it (Phase 3) — see
    # VariableRole above for why this is a list, not a single value.
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(30)), nullable=False, default=list)
    min_value: Mapped[float | None] = mapped_column(Numeric(18, 6))
    max_value: Mapped[float | None] = mapped_column(Numeric(18, 6))
    # Capped sample of distinct values for categorical variables — a
    # filter-dropdown data source, never treated as exhaustive. Null for
    # numeric/temporal variables, where a range (min/max above) is the
    # meaningful summary instead.
    distinct_values: Mapped[list[str] | None] = mapped_column(ARRAY(String(255)))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dataset: Mapped["Dataset"] = relationship(back_populates="variables")


class DatasetView(UUIDPKMixin, Base):
    """One row per dataset-detail-page view by a logged-in user — backs
    Overview's real "datasets viewed" stat (Master Plan §3 Phase 6 task 1).
    Anonymous views are not logged (no user_id to attribute them to).
    No uniqueness constraint: every view is logged; "distinct datasets
    viewed" is a COUNT(DISTINCT dataset_id) over this table, not a flag."""

    __tablename__ = "dataset_views"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
