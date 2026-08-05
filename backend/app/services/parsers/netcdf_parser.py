from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from app.services.parsers.base import (
    FileParser,
    ParsedFileMetadata,
    ParserError,
    ProcessedArtifact,
)

_LAT_NAMES = ("lat", "latitude", "y")
_LON_NAMES = ("lon", "long", "longitude", "x")
_TIME_NAMES = ("time", "date", "datetime")


def _find_coord(ds: xr.Dataset, names: tuple[str, ...]) -> str | None:
    lowered = {c.lower(): c for c in ds.coords}
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


class NetcdfParser(FileParser):
    extensions = ("nc", "netcdf", "nc4")

    def parse(self, path: Path) -> ParsedFileMetadata:
        try:
            with xr.open_dataset(path, decode_times=True) as ds:
                return self._extract(ds)
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(f"File is not a valid NetCDF dataset: {exc}") from exc

    def _extract(self, ds: xr.Dataset) -> ParsedFileMetadata:
        if len(ds.data_vars) == 0:
            raise ParserError("NetCDF file has no data variables")

        lat_name = _find_coord(ds, _LAT_NAMES)
        lon_name = _find_coord(ds, _LON_NAMES)
        time_name = _find_coord(ds, _TIME_NAMES)

        lat_min = lat_max = lon_min = lon_max = None
        if lat_name is not None:
            lat_values = ds[lat_name].values
            if lat_values.size > 0:
                lat_min, lat_max = float(np.nanmin(lat_values)), float(np.nanmax(lat_values))
        if lon_name is not None:
            lon_values = ds[lon_name].values
            if lon_values.size > 0:
                lon_min, lon_max = float(np.nanmin(lon_values)), float(np.nanmax(lon_values))

        temporal_start = temporal_end = None
        if time_name is not None:
            time_values = pd.to_datetime(ds[time_name].values)
            if len(time_values) > 0:
                temporal_start = pd.Timestamp(time_values.min()).date()
                temporal_end = pd.Timestamp(time_values.max()).date()

        dimensions = {str(k): int(v) for k, v in ds.sizes.items()}
        record_count = 1
        for v in dimensions.values():
            record_count *= max(v, 1)

        return ParsedFileMetadata(
            variables=list(ds.data_vars.keys()),
            dimensions=dimensions,
            spatial_lat_min=lat_min,
            spatial_lat_max=lat_max,
            spatial_lon_min=lon_min,
            spatial_lon_max=lon_max,
            temporal_start=temporal_start,
            temporal_end=temporal_end,
            record_count=record_count,
            extra={
                "attrs": {k: str(v) for k, v in ds.attrs.items()},
                "lat_coord": lat_name,
                "lon_coord": lon_name,
                "time_coord": time_name,
            },
        )

    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        output_path = output_dir / "processed.parquet"
        try:
            with xr.open_dataset(path, decode_times=True) as ds:
                # Flatten every data variable + its coordinates into one long
                # (tidy) DataFrame — the standard shape for the query-optimized
                # processed/ copy that catalog/visualize queries read from.
                df = ds.to_dataframe().reset_index()
                df.to_parquet(output_path, index=False)
                return ProcessedArtifact(
                    local_path=output_path,
                    content_type="application/vnd.apache.parquet",
                    file_extension="parquet",
                    row_count=len(df),
                )
        except Exception as exc:
            raise ParserError(f"Failed to convert NetCDF to Parquet: {exc}") from exc
