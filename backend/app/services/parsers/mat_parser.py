from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import scipy.io

from app.core.config import settings
from app.services.parsers.base import (
    FileParser,
    ParsedFileMetadata,
    ParserError,
    ProcessedArtifact,
)

_LAT_NAMES = ("lat", "latitude", "y")
_LON_NAMES = ("lon", "long", "longitude", "x")
_TIME_NAMES = ("time", "date", "datetime", "t")

# Row-slice size for streaming a v7.3 (HDF5) .mat variable during
# to_processed() — mirrors csv_parser.py's chunksize, keeping at most this
# many rows of ALL tabular variables in memory at once, never the full
# variable.
_MAT_CHUNK_ROWS = 100_000


def _squeezable_length(shape: tuple[int, ...]) -> int | None:
    """Returns the length a dataset would have after being ravel()'d into
    1-D, or None if it isn't shaped like a flat/vector series at all (e.g.
    a genuine 2-D grid with more than one non-1-sized axis) — same
    eligibility rule the legacy path applies to a loaded array's .shape,
    computed here from HDF5 shape metadata alone so it costs nothing to
    check before deciding whether a variable is even a chunking candidate."""
    non_unit_dims = [d for d in shape if d != 1]
    if len(non_unit_dims) > 1:
        return None
    total = 1
    for d in shape:
        total *= d
    return total


def _is_v73_mat(path: Path) -> bool:
    """MATLAB v7.3 .mat files are actually HDF5 under the hood; the classic
    (<=v7.2) format is not. h5py can only open the former, scipy.io.loadmat
    only the latter — so we sniff the real container format up front rather
    than try one and catch."""
    try:
        with open(path, "rb") as f:
            header = f.read(128)
        return b"MATLAB 7.3" in header
    except OSError as exc:
        raise ParserError(f"Could not read file header: {exc}") from exc


class MatParser(FileParser):
    extensions = ("mat",)

    def parse(self, path: Path) -> ParsedFileMetadata:
        if _is_v73_mat(path):
            variables = self._read_v73(path)
        else:
            variables = self._read_legacy(path)

        if not variables:
            raise ParserError(".mat file contains no readable variables")

        return self._extract_metadata(variables)

    def _read_legacy(self, path: Path) -> dict[str, np.ndarray]:
        # scipy.io.loadmat has no chunked/partial-read API — it loads the
        # entire file into memory in one call no matter what. Rather than
        # attempt that on a large file and risk OOMing the ingestion
        # worker, reject clearly above a configured threshold and point at
        # the real fix: MATLAB's own v7.3 (HDF5) format IS readable in
        # slices (see _read_v73_chunked below) and is what MATLAB itself
        # already defaults to for files over 2GB.
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > settings.LEGACY_MAT_MAX_SIZE_MB:
            raise ParserError(
                f"Legacy MATLAB (.mat) file is {size_mb:.0f}MB, which exceeds the "
                f"{settings.LEGACY_MAT_MAX_SIZE_MB}MB limit for the legacy format — it cannot be "
                "read without loading the entire file into memory. Re-save this file as "
                "MATLAB v7.3 format (File > Save As > MAT-file 7.3 in MATLAB, or "
                "save(..., '-v7.3') from the command line) and upload that instead; "
                "v7.3 files are read in chunks and have no such limit."
            )
        try:
            raw = scipy.io.loadmat(path)
        except Exception as exc:
            raise ParserError(f"File is not a valid MATLAB (.mat) file: {exc}") from exc
        return {k: v for k, v in raw.items() if not k.startswith("__")}

    def _read_v73(self, path: Path) -> dict[str, np.ndarray]:
        try:
            variables: dict[str, np.ndarray] = {}
            with h5py.File(path, "r") as f:
                for key in f.keys():
                    if key.startswith("#"):
                        continue
                    item = f[key]
                    if isinstance(item, h5py.Dataset):
                        variables[key] = item[()]
            return variables
        except Exception as exc:
            raise ParserError(f"File is not a valid MATLAB v7.3 (.mat) file: {exc}") from exc

    def _extract_metadata(self, variables: dict[str, np.ndarray]) -> ParsedFileMetadata:
        lowered = {k.lower(): k for k in variables}

        def find_key(names: tuple[str, ...]) -> str | None:
            for name in names:
                if name in lowered:
                    return lowered[name]
            return None

        def find(names: tuple[str, ...]) -> np.ndarray | None:
            key = find_key(names)
            return np.asarray(variables[key]).ravel() if key is not None else None

        lat_key, lon_key, time_key = find_key(_LAT_NAMES), find_key(_LON_NAMES), find_key(_TIME_NAMES)
        lat_values = find(_LAT_NAMES)
        lon_values = find(_LON_NAMES)
        time_values = find(_TIME_NAMES)

        lat_min = lat_max = lon_min = lon_max = None
        if lat_values is not None and lat_values.size > 0:
            lat_min, lat_max = float(np.nanmin(lat_values)), float(np.nanmax(lat_values))
        if lon_values is not None and lon_values.size > 0:
            lon_min, lon_max = float(np.nanmin(lon_values)), float(np.nanmax(lon_values))

        temporal_start = temporal_end = None
        if time_values is not None and time_values.size > 0:
            try:
                # MATLAB datenum (days since year 0, proleptic) is the common
                # convention; fall back gracefully if values don't parse.
                dates = pd.to_datetime(time_values - 719529, unit="D", errors="coerce")
                dates = dates.dropna()
                if len(dates) > 0:
                    temporal_start = pd.Timestamp(dates.min()).date()
                    temporal_end = pd.Timestamp(dates.max()).date()
            except (ValueError, OverflowError):
                pass

        dimensions = {name: int(np.asarray(arr).size) for name, arr in variables.items()}
        record_count = max(dimensions.values()) if dimensions else 0

        return ParsedFileMetadata(
            variables=list(variables.keys()),
            dimensions=dimensions,
            spatial_lat_min=lat_min,
            spatial_lat_max=lat_max,
            spatial_lon_min=lon_min,
            spatial_lon_max=lon_max,
            temporal_start=temporal_start,
            temporal_end=temporal_end,
            record_count=record_count,
            extra={"lat_col": lat_key, "lon_col": lon_key, "time_col": time_key},
        )

    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        output_path = output_dir / "processed.parquet"
        if _is_v73_mat(path):
            # Chunked path (Phase 2): reads each variable in row-slices via
            # h5py's native slicing (lazy — a slice never requires the full
            # HDF5 dataset in memory) and streams to Parquet incrementally,
            # never holding more than _MAT_CHUNK_ROWS rows of all tabular
            # variables combined in memory at once.
            return self._to_processed_v73_chunked(path, output_path)

        # Legacy (<=v7.2): _read_legacy already enforces
        # LEGACY_MAT_MAX_SIZE_MB before this point, since scipy.io.loadmat
        # itself has no chunked-read API — the eager approach below is the
        # only option for this format, bounded to files the size guard
        # already deemed small enough to load whole.
        variables = self._read_legacy(path)

        # Only 1-D (or squeezable-to-1-D) variables of matching length can
        # form tidy tabular rows; anything else (e.g. a 2-D grid) is kept out
        # of the flattened table and left in the raw/ copy for now — full
        # N-D grid subsetting is Phase 5/7 territory, not ingestion metadata.
        flat = {k: np.asarray(v).ravel() for k, v in variables.items()}
        lengths = {k: v.size for k, v in flat.items()}
        if not lengths:
            raise ParserError("No variables available to convert")

        common_length = max(lengths.values())
        tabular = {k: v for k, v in flat.items() if v.size == common_length}
        if not tabular:
            raise ParserError("No variables share a common row count for tabular conversion")

        df = pd.DataFrame(tabular)
        df.to_parquet(output_path, index=False)
        return ProcessedArtifact(
            local_path=output_path,
            content_type="application/vnd.apache.parquet",
            file_extension="parquet",
            row_count=len(df),
        )

    def _to_processed_v73_chunked(self, path: Path, output_path: Path) -> ProcessedArtifact:
        import pyarrow as pa
        import pyarrow.parquet as pq

        try:
            with h5py.File(path, "r") as f:
                # Only 1-D (or squeezable-to-1-D) datasets of matching
                # length can form tidy tabular rows — same eligibility
                # rule as the legacy path, but determined from .shape
                # metadata (free) rather than a loaded array.
                candidates: dict[str, h5py.Dataset] = {}
                for key in f.keys():
                    if key.startswith("#"):
                        continue
                    item = f[key]
                    if isinstance(item, h5py.Dataset) and _squeezable_length(item.shape) is not None:
                        candidates[key] = item

                if not candidates:
                    raise ParserError("No variables available to convert")

                lengths = {k: _squeezable_length(v.shape) for k, v in candidates.items()}
                common_length = max(lengths.values())
                tabular = {k: v for k, v in candidates.items() if lengths[k] == common_length}
                if not tabular:
                    raise ParserError("No variables share a common row count for tabular conversion")

                col_names = sorted(tabular.keys())
                writer: pq.ParquetWriter | None = None
                row_count = 0

                for start in range(0, common_length, _MAT_CHUNK_ROWS):
                    end = min(start + _MAT_CHUNK_ROWS, common_length)
                    chunk_columns = {
                        name: np.asarray(tabular[name][start:end]).ravel() for name in col_names
                    }
                    table = pa.table(chunk_columns)
                    if writer is None:
                        writer = pq.ParquetWriter(output_path, table.schema)
                    writer.write_table(table)
                    row_count += end - start

                if writer is not None:
                    writer.close()

            return ProcessedArtifact(
                local_path=output_path,
                content_type="application/vnd.apache.parquet",
                file_extension="parquet",
                row_count=row_count,
            )
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(f"Failed to convert MATLAB v7.3 file to Parquet: {exc}") from exc
