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
    artifact form is correct (Parquet vs. Zarr vs. raster) and which
    ParsedFileMetadata fields are populated.

    GRIDDED (Phase 5 — Storage & Query Architecture): a genuinely dense,
    regular array — NetCDF with real spatial grid dimensions, or a
    MATLAB struct matched by mat_gridded_struct.find_gridded_struct_field.
    Set explicitly by the parser at parse() time from the data's actual
    shape, never inferred later from element count — a small grid is
    still GRIDDED; there is no size below which flattening it into rows
    is correct (see PLAN.md Phase 5's "Guiding decision"). TABULAR
    remains for genuinely sparse/tabular data (CSV, non-gridded .mat,
    NetCDF whose coordinates don't form a real grid)."""

    TABULAR = "tabular"
    GRIDDED = "gridded"
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

    Phase 5 (Storage & Query Architecture): GRIDDED data always produces a
    Zarr store — `is_zarr=True` signals this so the ingestion pipeline
    knows NOT to attempt _write_dataset_records against it (that function
    reads a Parquet file; a Zarr-backed file has no tidy-table row concept
    at all, same reasoning as why raster/GeoTIFF is already skipped).

    A Zarr store is architecturally a directory of many chunk objects, not
    one blob. Phase 2 bridged this by zipping the whole store into one
    `.zarr.zip` object — simple, but StorageService.get() has no Range
    support, so reading it back meant downloading the entire store first,
    defeating the point of chunked storage. Phase 5 replaces that: a Zarr
    artifact is now written directly to `local_dir` (a real directory —
    zarr's own native on-disk layout, one file per chunk + per-array
    metadata), and the ingestion task uploads every file under it to a
    shared MinIO prefix (`processed_prefix()`) as individual objects,
    enabling genuine chunk-range reads via ordinary per-object GETs.
    `local_path` remains the field non-Zarr formats (Parquet, COG) use for
    their single-object artifact; `local_dir` is populated instead
    (`local_path` left None) when `is_zarr=True`.
    """

    local_path: Path | None = None
    local_dir: Path | None = None  # populated instead of local_path when is_zarr=True
    content_type: str = "application/octet-stream"
    file_extension: str = ""  # no leading dot, e.g. "parquet", "cog.tif" — unused when is_zarr=True
    row_count: int | None = None  # tabular (Parquet) only
    is_zarr: bool = False

    def __post_init__(self) -> None:
        if self.is_zarr:
            if self.local_dir is None:
                raise ValueError("ProcessedArtifact(is_zarr=True) requires local_dir")
        elif self.local_path is None:
            raise ValueError("ProcessedArtifact(is_zarr=False) requires local_path")


class FileParser(ABC):
    """Isolated per-format parser (Master Plan §3 Phase 2 task 3). Each
    implementation is self-contained — no shared state, no dependency on the
    ingestion pipeline — so new formats can be added later purely by
    registering another FileParser subclass.

    Extensibility note (formats beyond today's CSV/NetCDF/.mat/GeoTIFF):
    - Additional single-file gridded/raster formats slot in exactly like
      GeoTiffParser — implement parse()/to_processed() against DataShape.RASTER.
    - Zarr as an UPLOAD format (raw/ input) is still architecturally
      deferred — a Zarr store as the *source* file is a directory of many
      small chunk objects, not one blob, and would need the upload
      endpoint/raw_key()/storage layer to accept a directory tree as raw
      input. That remains future work.
    - Zarr as a PROCESSED/OUTPUT format (Phase 5) IS fully implemented:
      any parser detecting DataShape.GRIDDED data writes a real Zarr store
      (multi-object, one file per chunk) via `ProcessedArtifact.local_dir`
      — see that dataclass's docstring for the object-per-chunk layout and
      why it replaced Phase 2's zipped-single-object bridge.
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
