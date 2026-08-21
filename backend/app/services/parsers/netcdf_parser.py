from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from app.core.config import settings
from app.services.parsers.base import (
    DataShape,
    FileParser,
    ParsedFileMetadata,
    ParserError,
    ProcessedArtifact,
)

_LAT_NAMES = ("lat", "latitude", "y")
_LON_NAMES = ("lon", "long", "longitude", "x")
# "valid_time" is ERA5/Copernicus reanalysis data's standard time coordinate
# name (used in place of plain "time" since a recent CDS API convention
# change) — real files from that source (e.g. Model Wave Data's
# ERA5_Wind_Monthly_2010_2024.nc) would otherwise have no detected time
# coordinate at all despite genuinely having one.
_TIME_NAMES = ("time", "date", "datetime", "valid_time")
# Oceanographic Profiles module: vertical-coordinate detection. Only matched
# against ds.coords (like lat/lon/time above), never ds.dims alone — a
# dataset can have a "depth" dimension with no matching 1-D coordinate
# variable, which _find_coord correctly leaves undetected rather than
# guessing.
_DEPTH_NAMES = ("depth", "depth_m", "z", "level", "pressure")

# Chunk-axis strategy: chunk along the detected time dimension when one
# exists (matches the stated preference for oceanographic/model data,
# where "give me the next N time steps" is the natural access pattern
# for both ingestion and later Visualize time-series queries). Falls back
# to dask's own "auto" chunking (spatial-axis-based) for a file with no
# detected time dimension at all (e.g. a static multidimensional field).
# Sourced from Settings.INGESTION_ZARR_TIME_CHUNK_SIZE — see that field's
# docstring for the real-file benchmark (object count, write time, and
# query performance) behind its default value.
_TIME_CHUNK_SIZE = settings.INGESTION_ZARR_TIME_CHUNK_SIZE


def _find_coord(ds: xr.Dataset, names: tuple[str, ...]) -> str | None:
    lowered = {c.lower(): c for c in ds.coords}
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


def _total_elements(sizes: dict) -> int:
    total = 1
    for v in sizes.values():
        total *= max(int(v), 1)
    return total


def _is_gridded(ds: xr.Dataset, *, time_name: str | None) -> bool:
    """Shape-based routing (PLAN.md Phase 5): a dataset is GRIDDED if any
    real data variable is indexed by more than one non-time dimension —
    genuine multi-axis array indexing (e.g. (time, lat, lon)), not a
    single shared "observation" index the way a tabular/per-row NetCDF
    (e.g. (obs,) with lat/lon as same-length 1-D coordinate arrays, not
    axes the data variable is actually indexed by) would be. Determined
    from dimensionality alone, never element count — a 4x4 grid is just
    as GRIDDED as a 4000x4000 one; see DataShape.GRIDDED's docstring."""
    for var in ds.data_vars.values():
        non_time_dims = [d for d in var.dims if d != time_name]
        if len(non_time_dims) >= 2:
            return True
    return False


class NetcdfParser(FileParser):
    extensions = ("nc", "netcdf", "nc4")

    def parse(self, path: Path) -> ParsedFileMetadata:
        try:
            # chunks="auto" (Phase 2): opens with Dask-backed arrays rather
            # than eager numpy — data variables are never read here at all
            # (only small coordinate arrays for lat/lon/time extent below),
            # so this is already safe even before to_processed()'s heavier
            # conversion step; using the same chunked-open call in both
            # places keeps behavior consistent regardless of file size.
            with xr.open_dataset(path, decode_times=True, chunks="auto") as ds:
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
        depth_name = _find_coord(ds, _DEPTH_NAMES)

        # Coordinate arrays are always tiny relative to the full dataset
        # (a lat/lon/time axis, not the gridded data itself) — .values
        # here loads only that one small array, never a data variable.
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

        depth_min = depth_max = None
        depth_convention = None
        if depth_name is not None:
            depth_values = ds[depth_name].values
            if depth_values.size > 0:
                depth_min = float(np.nanmin(depth_values))
                depth_max = float(np.nanmax(depth_values))
                depth_convention = "assumed_positive_down" if depth_min >= 0 else "assumed_negative_up"

        dimensions = {str(k): int(v) for k, v in ds.sizes.items()}
        record_count = _total_elements(dimensions)
        shape = DataShape.GRIDDED if _is_gridded(ds, time_name=time_name) else DataShape.TABULAR

        return ParsedFileMetadata(
            shape=shape,
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
                "lat_col": lat_name,
                "lon_col": lon_name,
                "time_col": time_name,
                "depth_col": depth_name,
                "depth_min": depth_min,
                "depth_max": depth_max,
                "depth_convention": depth_convention,
            },
        )

    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        try:
            with xr.open_dataset(path, decode_times=True, chunks="auto") as probe:
                time_name = _find_coord(probe, _TIME_NAMES)
                gridded = _is_gridded(probe, time_name=time_name)

            if gridded:
                return self._to_zarr(path, output_dir, time_name=time_name)
            return self._to_parquet(path, output_dir, time_name=time_name)
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(f"Failed to convert NetCDF: {exc}") from exc

    def _open_chunked(self, path: Path, *, time_name: str | None) -> xr.Dataset:
        """Opens with Dask-backed arrays, chunked along the detected time
        dimension when one exists (Phase 2's stated chunk-axis
        preference) — every subsequent read against this Dataset pulls in
        at most one time-chunk's worth of data per variable at a time,
        never the whole array."""
        if time_name is not None:
            return xr.open_dataset(path, decode_times=True, chunks={time_name: _TIME_CHUNK_SIZE})
        return xr.open_dataset(path, decode_times=True, chunks="auto")

    def _to_parquet(self, path: Path, output_dir: Path, *, time_name: str | None) -> ProcessedArtifact:
        import pyarrow as pa
        import pyarrow.parquet as pq

        output_path = output_dir / "processed.parquet"
        writer: pq.ParquetWriter | None = None
        row_count = 0

        with self._open_chunked(path, time_name=time_name) as ds:
            if time_name is not None and ds.sizes.get(time_name, 0) > 0:
                # Stream one time-chunk at a time: to_dataframe() on a
                # _TIME_CHUNK_SIZE-step slice is bounded regardless of the
                # full dataset's total time extent — this is what replaces
                # the old single ds.to_dataframe() call on the WHOLE
                # dataset, which is exactly what made a large NetCDF
                # certain to OOM regardless of available RAM.
                time_len = ds.sizes[time_name]
                for start in range(0, time_len, _TIME_CHUNK_SIZE):
                    end = min(start + _TIME_CHUNK_SIZE, time_len)
                    chunk_ds = ds.isel({time_name: slice(start, end)})
                    df = chunk_ds.to_dataframe().reset_index()
                    table = pa.Table.from_pandas(df, preserve_index=False)
                    if writer is None:
                        writer = pq.ParquetWriter(output_path, table.schema)
                    writer.write_table(table)
                    row_count += len(df)
            else:
                # No time dimension to chunk along — fall back to a single
                # to_dataframe() call. Safe here specifically because this
                # method is only reached when to_processed()'s shape check
                # already decided the file is TABULAR (a real GRIDDED
                # dataset — multi-axis-indexed, any size — always goes to
                # _to_zarr instead, never here).
                df = ds.to_dataframe().reset_index()
                table = pa.Table.from_pandas(df, preserve_index=False)
                writer = pq.ParquetWriter(output_path, table.schema)
                writer.write_table(table)
                row_count = len(df)

        if writer is not None:
            writer.close()

        return ProcessedArtifact(
            local_path=output_path,
            content_type="application/vnd.apache.parquet",
            file_extension="parquet",
            row_count=row_count,
        )

    def _to_zarr(self, path: Path, output_dir: Path, *, time_name: str | None) -> ProcessedArtifact:
        """Writes the dataset as a chunked Zarr store — the correct
        representation for GRIDDED (genuinely multi-axis-indexed) data,
        regardless of size (PLAN.md Phase 5 — shape-based, not size-based,
        routing). xarray's to_zarr() writes chunk-by-chunk internally
        (that's the whole point of the Zarr chunked-array format), so
        this never materializes the full dataset in memory the way
        ds.to_dataframe() on an unchunked open would.

        Phase 5: writes real Zarr directory layout (one file per chunk +
        per-array metadata) rather than Phase 2's zipped single object —
        the ingestion task uploads every file under this directory to
        MinIO as individual objects under a shared prefix, enabling
        genuine chunk-range reads. See ProcessedArtifact's docstring."""
        zarr_dir = output_dir / "processed.zarr"

        with self._open_chunked(path, time_name=time_name) as ds:
            ds.to_zarr(zarr_dir, mode="w")

        return ProcessedArtifact(
            local_dir=zarr_dir,
            content_type="application/octet-stream",
            row_count=None,
            is_zarr=True,
        )
