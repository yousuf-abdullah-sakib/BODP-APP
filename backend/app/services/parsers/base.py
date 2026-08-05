from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path


class ParserError(Exception):
    """Raised when a file cannot be parsed as its declared format — e.g. a
    .csv that isn't valid CSV, a .nc that isn't a real NetCDF file. Must
    result in the upload being marked 'failed' with a clear reason, never
    silently accepted (Master Plan §3 Phase 2 quality check)."""


class DataShape(StrEnum):
    """What kind of data a parsed file represents. Drives which processed
    artifact form is correct (Parquet vs. raster) and which ParsedFileMetadata
    fields are populated — added when generalizing the pipeline for GeoTIFF
    (raster) alongside the original tabular formats (CSV/NetCDF/.mat)."""

    TABULAR = "tabular"
    RASTER = "raster"


@dataclass
class ParsedFileMetadata:
    """What every format parser extracts, regardless of the underlying file
    format — this is the common shape the ingestion pipeline (Phase 2 task 4)
    writes into `dataset_files.file_metadata` / auto-detected extent fields.

    `variables`/`record_count` are the tabular-shaped fields (rows/columns).
    `bands`/`pixel_width`/`pixel_height` are the raster-shaped equivalents,
    populated instead when `shape == DataShape.RASTER`. Both halves share the
    same spatial/temporal extent fields since bounding-box detection and
    PostGIS storage work identically regardless of shape.
    """

    shape: DataShape = DataShape.TABULAR

    # --- Tabular fields (CSV, NetCDF, .mat) ---
    variables: list[str] = field(default_factory=list)
    dimensions: dict[str, int] = field(default_factory=dict)
    record_count: int | None = None

    # --- Raster fields (GeoTIFF/COG; future: any gridded imagery format) ---
    bands: list[str] = field(default_factory=list)
    pixel_width: int | None = None
    pixel_height: int | None = None

    # --- Shared ---
    spatial_lat_min: float | None = None
    spatial_lat_max: float | None = None
    spatial_lon_min: float | None = None
    spatial_lon_max: float | None = None
    temporal_start: date | None = None
    temporal_end: date | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessedArtifact:
    """Describes the query-optimized artifact a parser produced for the
    `processed/` storage tier, plus enough about its shape for the ingestion
    task to record sensible metadata without needing format-specific
    knowledge.

    Introduced when generalizing FileParser.to_parquet() (Parquet-only) into
    FileParser.to_processed() — tabular formats still produce Parquet;
    GeoTIFF produces a re-tiled Cloud-Optimized GeoTIFF instead, and the
    ingestion pipeline no longer assumes a row count applies to every format.
    """

    local_path: Path
    content_type: str
    file_extension: str  # no leading dot, e.g. "parquet" or "cog.tif"
    row_count: int | None = None  # tabular only


class FileParser(ABC):
    """Isolated per-format parser (Master Plan §3 Phase 2 task 3). Each
    implementation is self-contained — no shared state, no dependency on the
    ingestion pipeline — so new formats can be added later purely by
    registering another FileParser subclass.

    Extensibility note (formats beyond today's CSV/NetCDF/.mat/GeoTIFF):
    - Additional single-file gridded/raster formats slot in exactly like
      GeoTiffParser — implement parse()/to_processed() against DataShape.RASTER.
    - Zarr is architecturally different: a Zarr "file" is actually a
      directory of many small chunk objects, not one blob. Supporting it
      will need the upload endpoint and raw_key()/storage layer to accept a
      directory tree (or a single .zip/.zarr.zip container, which current
      tooling can read directly without unpacking — the simpler bridge into
      today's single-object pipeline if/when Zarr support is added) rather
      than a single-file upload. That is a storage/upload-layer change, not
      a FileParser one — this interface itself does not need to change for it.
    """

    #: File extensions (lowercase, no leading dot) this parser declares
    #: itself capable of handling — used by the format registry.
    extensions: tuple[str, ...] = ()

    #: Whether this format represents tabular observations or a raster grid
    #: — read by the ingestion task to decide how to interpret the returned
    #: ParsedFileMetadata without format-specific branching.
    data_shape: DataShape = DataShape.TABULAR

    @abstractmethod
    def parse(self, path: Path) -> ParsedFileMetadata:
        """Read the file at `path` and extract metadata. Must raise
        ParserError (not let a raw library exception escape) on anything
        that indicates the file doesn't actually match its declared format."""

    @abstractmethod
    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        """Write a query-optimized copy into `output_dir` for the
        `processed/` storage tier (Master Plan §3 Phase 2 task 4) and
        describe what was produced. Tabular formats write Parquet; raster
        formats write a re-tiled Cloud-Optimized GeoTIFF. The caller (the
        ingestion task) uploads whatever `ProcessedArtifact.local_path`
        points to — it does not need to know which."""
