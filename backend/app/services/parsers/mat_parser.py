from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import scipy.io

from app.services.parsers.base import (
    FileParser,
    ParsedFileMetadata,
    ParserError,
    ProcessedArtifact,
)

_LAT_NAMES = ("lat", "latitude", "y")
_LON_NAMES = ("lon", "long", "longitude", "x")
_TIME_NAMES = ("time", "date", "datetime", "t")


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

        def find(names: tuple[str, ...]) -> np.ndarray | None:
            for name in names:
                if name in lowered:
                    return np.asarray(variables[lowered[name]]).ravel()
            return None

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
        )

    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        if _is_v73_mat(path):
            variables = self._read_v73(path)
        else:
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

        output_path = output_dir / "processed.parquet"
        df = pd.DataFrame(tabular)
        df.to_parquet(output_path, index=False)
        return ProcessedArtifact(
            local_path=output_path,
            content_type="application/vnd.apache.parquet",
            file_extension="parquet",
            row_count=len(df),
        )
