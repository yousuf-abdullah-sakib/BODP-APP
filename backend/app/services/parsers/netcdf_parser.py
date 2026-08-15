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
# "valid_time" is ERA5/Copernicus reanalysis data's standard time coordinate
# name (used in place of plain "time" since a recent CDS API convention
# change) — real files from that source (e.g. Model Wave Data's
# ERA5_Wind_Monthly_2010_2024.nc) would otherwise have no detected time
# coordinate at all despite genuinely having one.
_TIME_NAMES = ("time", "date", "datetime", "valid_time")

# Phase 2 chunking/output-format constants — the two open design decisions
# PLAN.md flagged as needing to be settled during implementation, not
# guessed at up front:
#
# 1. Chunk-axis strategy: chunk along the detected time dimension when one
#    exists (matches the stated preference for oceanographic/model data,
#    where "give me the next N time steps" is the natural access pattern
#    for both ingestion and later Visualize time-series queries) — sized
#    so each chunk stays comfortably small regardless of how large the
#    spatial grid per time step is. Falls back to dask's own "auto"
#    chunking (spatial-axis-based) for a file with no detected time
#    dimension at all (e.g. a static multidimensional field).
_TIME_CHUNK_SIZE = 24  # e.g. 24 monthly steps, or 24 hourly steps — small either way

# 2. Zarr-vs-Parquet output heuristic: a flattened tidy Parquet table is
#    the right shape for the catalog/DatasetRecord query path (Phase 2's
#    _write_dataset_records reads exactly this), but flattening every
#    element of a genuinely large multidimensional grid into a long/tidy
#    table is both wasteful (huge row count, mostly repeated coordinate
#    values) and structurally the wrong representation for chunked/
#    indexable array access. Above this many total elements (product of
#    every dimension's size), to_processed() switches to writing a
#    chunked Zarr store instead of attempting a flattened Parquet
#    conversion. 50 million elements is a deliberately conservative
#    threshold — a modest laptop-scale to_dataframe() call already
#    struggles well before that; the real intent is "don't even attempt
#    the tidy-table flatten path once the file is unambiguously grid-
#    shaped and large," not to finely tune where Parquet stops being
#    ideal.
_ZARR_THRESHOLD_ELEMENTS = 50_000_000


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

        dimensions = {str(k): int(v) for k, v in ds.sizes.items()}
        record_count = _total_elements(dimensions)

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
                "lat_col": lat_name,
                "lon_col": lon_name,
                "time_col": time_name,
            },
        )

    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        try:
            with xr.open_dataset(path, decode_times=True, chunks="auto") as probe:
                total_elements = _total_elements(dict(probe.sizes))
                time_name = _find_coord(probe, _TIME_NAMES)

            if total_elements > _ZARR_THRESHOLD_ELEMENTS:
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
                # method is only reached when to_processed()'s size
                # heuristic already decided the file is small enough
                # (below _ZARR_THRESHOLD_ELEMENTS) not to need Zarr; a
                # large timeless file goes to _to_zarr instead.
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
        """Writes the dataset as a chunked Zarr store — appropriate for
        large, genuinely multidimensional data where a flattened tidy
        table would be both huge and the wrong representation for
        indexable array access. xarray's to_zarr() writes chunk-by-chunk
        internally (that's the whole point of the Zarr chunked-array
        format), so this never materializes the full dataset in memory
        the way ds.to_dataframe() on an unchunked open would."""
        import shutil
        import zipfile

        zarr_dir = output_dir / "processed.zarr"
        output_path = output_dir / "processed.zarr.zip"

        with self._open_chunked(path, time_name=time_name) as ds:
            ds.to_zarr(zarr_dir, mode="w")

        # Zip the store into one object so it fits the existing
        # single-file storage.put() pipeline unchanged (see
        # ProcessedArtifact.is_zarr's docstring) — walks the store's own
        # directory tree, never loads chunk contents through Python
        # (shutil/zipfile stream file-to-file). Paths are relative to
        # zarr_dir ITSELF (not its parent) so the Zarr group sits at the
        # zip's root — zarr.storage.ZipStore/xarray's open_zarr expect the
        # group's own files (.zgroup, .zattrs, etc.) at that root, not
        # nested under a "processed.zarr/" prefix.
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_STORED) as zf:
            for file_path in zarr_dir.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(zarr_dir))
        shutil.rmtree(zarr_dir, ignore_errors=True)

        return ProcessedArtifact(
            local_path=output_path,
            content_type="application/zip",
            file_extension="zarr.zip",
            row_count=None,
            is_zarr=True,
        )
