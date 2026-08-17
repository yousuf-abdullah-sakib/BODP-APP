"""PLAN.md Phase 5 (Storage & Query Architecture) — queries a Zarr store
directly from MinIO via xarray/fsspec, for DatasetFile rows with
storage_kind=CHUNKED_ARRAY. This is the gridded-data half of the routing
layer catalog_service.py/visualize_service.py each add in front of their
existing DatasetRecord-based query functions; those existing functions
remain completely unmodified for storage_kind=ROW_RECORDS (legacy) data.

Chunk-range reads: opening via fsspec's S3 mapper (verified working
against this project's real MinIO before this module was written — a
Zarr store's per-chunk objects are fetched lazily/individually by
xarray/dask as a query actually touches them, not all at once) is what
makes this genuinely different from a full-object download — the whole
reason PLAN.md Phase 5 moved Zarr's processed-artifact layout from one
zipped object to one-object-per-chunk under a shared prefix.
"""

import uuid
from dataclasses import dataclass
from datetime import date as date_type

import numpy as np
import s3fs
import xarray as xr

from app.core.config import settings
from app.models.catalog import DatasetFile, QualityFlag, StorageBackend
from app.services.catalog_service import RecordsFilter


def _s3_filesystem(*, storage_backend: str) -> s3fs.S3FileSystem:
    """Same endpoint/credential resolution as tabular_query_service's
    DuckDB setup and storage/registry.py's boto3 backends — one more
    consumer of the same four config values, via a third library this
    time (s3fs), all pointed at the identical MinIO/cloud endpoint."""
    if storage_backend == StorageBackend.VPS_MINIO.value:
        endpoint = settings.STORAGE_VPS_ENDPOINT_URL
        access_key = settings.STORAGE_VPS_ACCESS_KEY
        secret_key = settings.STORAGE_VPS_SECRET_KEY
    elif storage_backend == StorageBackend.CLOUD.value:
        endpoint = settings.STORAGE_CLOUD_ENDPOINT_URL
        access_key = settings.STORAGE_CLOUD_ACCESS_KEY
        secret_key = settings.STORAGE_CLOUD_SECRET_KEY
    else:
        raise ValueError(f"Unknown storage backend: {storage_backend!r}")

    if not endpoint:
        raise ValueError(f"Storage backend {storage_backend!r} has no endpoint configured")

    return s3fs.S3FileSystem(
        key=access_key,
        secret=secret_key,
        client_kwargs={"endpoint_url": endpoint},
        use_ssl=endpoint.startswith("https://"),
    )


def _open_zarr(dataset_file: DatasetFile) -> xr.Dataset:
    file_metadata = dataset_file.file_metadata or {}
    bucket = file_metadata.get("processed_bucket")
    prefix = file_metadata.get("processed_prefix")
    if not bucket or not prefix:
        raise ValueError(f"DatasetFile {dataset_file.id} has no processed_prefix/processed_bucket recorded")

    fs = _s3_filesystem(storage_backend=dataset_file.storage_backend)
    mapper = fs.get_mapper(f"{bucket}/{prefix}")
    return xr.open_zarr(mapper)


def open_zarr_dataset(dataset_file: DatasetFile) -> xr.Dataset:
    """Public entry point for opening a CHUNKED_ARRAY DatasetFile's Zarr
    store as an xarray Dataset — used by worker/tasks/extraction.py's
    Zarr-aware extraction path (PLAN.md Phase 5) to reuse the exact same
    chunk-range-read mechanism (fsspec/s3fs multi-object mapper) this
    module's own query functions rely on, rather than re-deriving it."""
    return _open_zarr(dataset_file)


def _lat_lon_names(ds: xr.Dataset) -> tuple[str | None, str | None]:
    lat_name = next((c for c in ("lat", "latitude", "y") if c in ds.coords), None)
    lon_name = next((c for c in ("lon", "longitude", "x") if c in ds.coords), None)
    return lat_name, lon_name


def _apply_bbox(ds: xr.Dataset, f: RecordsFilter | None, *, lat_name: str | None, lon_name: str | None) -> xr.Dataset:
    """Subsets by bounding box via xarray's own .where() against the
    lat/lon coordinate arrays — the gridded equivalent of catalog_
    service's ST_Intersects/tabular_query_service's DuckDB spatial
    predicate. lat/lon here are 2-D (per-cell) coordinates for a real
    curvilinear grid (see mat_gridded_struct.py), so a boolean mask
    rather than a simple .sel() slice is the correct/general subsetting
    mechanism — works identically for a 1-D regular-grid case too."""
    if f is None or None in (f.lat_min, f.lat_max, f.lon_min, f.lon_max):
        return ds
    if lat_name is None or lon_name is None:
        return ds
    mask = (
        (ds[lat_name] >= f.lat_min)
        & (ds[lat_name] <= f.lat_max)
        & (ds[lon_name] >= f.lon_min)
        & (ds[lon_name] <= f.lon_max)
    )
    # .where(..., drop=True) does boolean indexing internally, which
    # xarray refuses on a dask-backed (lazy-chunked) array — "Indexing
    # with a boolean dask array is not allowed" — confirmed via a real
    # live upload of a large (12M-element) Zarr-backed dataset, not
    # triggered by this module's small hand-built test fixtures. The
    # mask itself is cheap (just the lat/lon coordinate arrays, not the
    # full data), so computing it eagerly here is the correct fix, not
    # a scalability compromise.
    return ds.where(mask.compute(), drop=True)


def _apply_date_range(ds: xr.Dataset, f: RecordsFilter | None) -> xr.Dataset:
    if f is None or "time" not in ds.coords:
        return ds
    if f.date_from:
        ds = ds.sel(time=slice(str(f.date_from), None))
    if f.date_to:
        ds = ds.sel(time=slice(None, str(f.date_to)))
    return ds


@dataclass
class GriddedPreviewRow:
    """Duck-typed equivalent of a DatasetRecord row, sourced from a Zarr
    grid cell rather than a SQL row — same attribute set as
    tabular_query_service.TabularPreviewRow / the DatasetRecordPreview
    response schema (from_attributes=True), so the routing layer treats
    both interchangeably."""

    id: uuid.UUID
    time: date_type | None
    location: str | None
    depth_m: float | None
    parameter: str
    value: float
    unit: str | None
    platform: str | None
    format: str | None
    processing_level: str | None
    quality_flag: str


@dataclass
class GriddedSpatialPoint:
    station: str
    lat: float
    lon: float
    value: float


def get_spatial_points(
    dataset_file: DatasetFile, parameter: str, f: RecordsFilter | None
) -> list[GriddedSpatialPoint]:
    """The gridded equivalent of tabular_query_service.get_spatial_points
    — latest (last-timestep) value per grid cell, labeled by its own
    lat/lon since a Zarr grid has no Station join either. Every cell is
    returned as one "point" (unlike tabular data's per-row stations, a
    grid's points ARE its cells) — for a large grid this can be many
    points, matching how the legacy path already returns one point per
    station with no cap; Visualize's IDW/interpolation step downstream
    handles arbitrary point counts already."""
    ds = _open_zarr(dataset_file)
    try:
        if parameter not in ds.data_vars:
            return []
        lat_name, lon_name = _lat_lon_names(ds)
        if lat_name is None or lon_name is None:
            return []

        filtered = _apply_date_range(_apply_bbox(ds, f, lat_name=lat_name, lon_name=lon_name), f)
        data_array = filtered[parameter]
        if "time" in data_array.dims:
            data_array = data_array.isel(time=-1)

        lat_vals = np.asarray(filtered[lat_name].values)
        lon_vals = np.asarray(filtered[lon_name].values)
        values = np.asarray(data_array.values)

        points: list[GriddedSpatialPoint] = []
        if lat_vals.ndim == 1 and lon_vals.ndim == 1 and values.ndim == 2:
            for i, lat in enumerate(lat_vals):
                for j, lon in enumerate(lon_vals):
                    v = values[i, j]
                    if v is not None and not (isinstance(v, float) and np.isnan(v)):
                        points.append(
                            GriddedSpatialPoint(station=f"{lat},{lon}", lat=float(lat), lon=float(lon), value=float(v))
                        )
        else:
            # 2-D curvilinear coords — lat/lon/value all share the same shape.
            flat_lat, flat_lon, flat_val = lat_vals.ravel(), lon_vals.ravel(), values.ravel()
            for lat, lon, v in zip(flat_lat, flat_lon, flat_val, strict=True):
                if v is not None and not (isinstance(v, float) and np.isnan(v)):
                    points.append(
                        GriddedSpatialPoint(station=f"{lat},{lon}", lat=float(lat), lon=float(lon), value=float(v))
                    )
        return points
    finally:
        ds.close()


def get_raw_values(
    dataset_file: DatasetFile, parameter: str, f: RecordsFilter | None
) -> list[tuple[str | None, date_type | None, float]]:
    """The gridded equivalent of tabular_query_service.get_raw_values —
    every matching (cell_label, time, value) triple, spatially averaged
    per timestep (a grid cell isn't a "station" with its own independent
    time series the way tabular data's rows are; Comparison/Statistics'
    per-source-label grouping is not a meaningful concept for a single
    grid, so every value here shares one synthesized label per file,
    matching how gridded_query_service.get_timeseries_aggregate already
    treats an entire grid as one spatial-mean series)."""
    ds = _open_zarr(dataset_file)
    try:
        if parameter not in ds.data_vars or "time" not in ds.coords:
            return []
        lat_name, lon_name = _lat_lon_names(ds)
        filtered = _apply_date_range(_apply_bbox(ds, f, lat_name=lat_name, lon_name=lon_name), f)
        data_array = filtered[parameter]
        spatial_dims = [d for d in data_array.dims if d != "time"]
        spatial_mean = data_array.mean(dim=spatial_dims) if spatial_dims else data_array
        spatial_mean = spatial_mean.compute()

        label = f"{dataset_file.id}"
        return [
            (label, date_type.fromisoformat(np.datetime_as_string(t, unit="D")), float(v))
            for t, v in zip(spatial_mean["time"].values, spatial_mean.values, strict=True)
            if not np.isnan(v)
        ]
    finally:
        ds.close()


def get_matching_record_counts(dataset_file: DatasetFile, f: RecordsFilter) -> tuple[int, int]:
    """Mirrors catalog_service.get_matching_record_counts's return shape:
    (matching_count, dataset_total_count) for THIS ONE FILE, counting
    grid elements (one "record" per cell x timestep, matching what
    ParsedFileMetadata.record_count already means for gridded data —
    see mat_parser.py/netcdf_parser.py) rather than SQL rows, since none
    exist. Only the single detected variable this file registered is
    counted per element (matches how a tabular file's variable columns
    each contribute their own DatasetRecord row per observation)."""
    ds = _open_zarr(dataset_file)
    try:
        variables = list(dataset_file.file_metadata.get("variables") or ds.data_vars.keys())
        lat_name, lon_name = _lat_lon_names(ds)

        total = 0
        for var in variables:
            if var in ds.data_vars:
                total += int(np.prod(ds[var].shape))

        if f.parameters and not (set(f.parameters) & set(variables)):
            return 0, total

        filtered = _apply_date_range(_apply_bbox(ds, f, lat_name=lat_name, lon_name=lon_name), f)
        matching = 0
        selected_vars = [v for v in variables if not f.parameters or v in f.parameters]
        for var in selected_vars:
            if var in filtered.data_vars:
                matching += int(filtered[var].count().values)

        return matching, total
    finally:
        ds.close()


def get_filtered_records(
    dataset_file: DatasetFile, f: RecordsFilter, *, preview_limit: int
) -> tuple[list[GriddedPreviewRow], int, int, dict[str, int]]:
    """Mirrors catalog_service.get_filtered_records's return shape:
    (preview_rows, matching_count, dataset_total_count,
    quality_breakdown). Every gridded cell is implicitly "normal"
    quality (no per-cell QC flag concept exists for array data today —
    matches how ingestion never wrote a quality_flag for gridded data
    even when it went through DatasetRecord in the pre-Phase-5 world)."""
    ds = _open_zarr(dataset_file)
    try:
        file_metadata = dataset_file.file_metadata or {}
        variables = list(file_metadata.get("variables") or ds.data_vars.keys())
        units = file_metadata.get("units")
        lat_name, lon_name = _lat_lon_names(ds)

        matching, total = get_matching_record_counts(dataset_file, f)

        filtered = _apply_date_range(_apply_bbox(ds, f, lat_name=lat_name, lon_name=lon_name), f)
        selected_vars = [v for v in variables if not f.parameters or v in f.parameters]

        preview_rows: list[GriddedPreviewRow] = []
        for var in selected_vars:
            if var not in filtered.data_vars or len(preview_rows) >= preview_limit:
                break
            # A bounded, lazy slice — .isel() on the first few timesteps
            # (or the whole array if there's no time axis) keeps this from
            # ever pulling the full grid into memory just to build a
            # capped preview table.
            data_array = filtered[var]
            if "time" in data_array.dims:
                data_array = data_array.isel(time=slice(0, min(3, data_array.sizes.get("time", 0)) or None))
            values = data_array.values
            flat_values = np.asarray(values).ravel()
            time_values = filtered["time"].values if "time" in filtered.coords else None

            for value in flat_values:
                if len(preview_rows) >= preview_limit:
                    break
                if value is None or (isinstance(value, float) and np.isnan(value)):
                    continue
                row_time = None
                if time_values is not None and time_values.size > 0:
                    row_time = np.datetime_as_string(time_values[0], unit="D")
                    row_time = date_type.fromisoformat(row_time)
                preview_rows.append(
                    GriddedPreviewRow(
                        id=uuid.uuid4(),
                        time=row_time,
                        location=None,
                        depth_m=None,
                        parameter=var,
                        value=float(value),
                        unit=units,
                        platform=None,
                        format=dataset_file.file_format,
                        processing_level=None,
                        quality_flag=QualityFlag.NORMAL.value,
                    )
                )

        quality_breakdown = {"normal": matching, "caution": 0, "alert": 0}
        return preview_rows, matching, total, quality_breakdown
    finally:
        ds.close()


def get_timeseries_aggregate(
    dataset_file: DatasetFile, *, parameter: str, resolution: str, f: RecordsFilter | None = None
) -> list[tuple[date_type, float]]:
    """Time-bucketed mean for Visualize's time-series module, computed
    via xarray's own resample()/groupby() rather than SQL date_trunc+AVG
    — the gridded equivalent of visualize_service.get_timeseries's
    DatasetRecord query. Returns [(bucket_date, mean_value), ...],
    matching the shape get_timeseries melts into SeriesPointSchema rows
    for either backend identically."""
    ds = _open_zarr(dataset_file)
    try:
        if parameter not in ds.data_vars or "time" not in ds.coords:
            return []

        lat_name, lon_name = _lat_lon_names(ds)
        filtered = _apply_date_range(_apply_bbox(ds, f, lat_name=lat_name, lon_name=lon_name), f)
        data_array = filtered[parameter]

        # Reduce spatial dimensions FIRST, then resample over time —
        # doing it in the opposite order (resample(...).mean(dim=[...]))
        # was found to corrupt the resulting time coordinate's dtype
        # (silently became a plain int64 index instead of datetime64),
        # confirmed via direct reproduction before this fix; this order
        # is also the numerically sensible one (average each timestep's
        # grid first, then bucket-average timesteps).
        spatial_dims = [d for d in data_array.dims if d != "time"]
        spatial_mean = data_array.mean(dim=spatial_dims) if spatial_dims else data_array

        freq = {"daily": "1D", "monthly": "1MS", "seasonal": "1QS", "annual": "1YS"}[resolution]
        resampled = spatial_mean.resample(time=freq).mean()
        resampled = resampled.compute()

        return [
            (date_type.fromisoformat(np.datetime_as_string(t, unit="D")), float(v))
            for t, v in zip(resampled["time"].values, resampled.values, strict=True)
            if not np.isnan(v)
        ]
    finally:
        ds.close()
