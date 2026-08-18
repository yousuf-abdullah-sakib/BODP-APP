import hashlib
import json
import math
import uuid
from calendar import month_abbr
from collections import defaultdict
from datetime import date

import numpy as np
from fastapi import HTTPException
from scipy.interpolate import NearestNDInterpolator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get_json, cache_set_json
from app.core.config import settings
from app.models.admin import SiteSettings
from app.models.catalog import (
    Dataset,
    DatasetCategory,
    DatasetFile,
    DatasetRecord,
    DatasetVariable,
    Station,
    StorageKind,
)
from app.models.visualize import VisualizationJob, VizJobStatus
from app.schemas.visualize import (
    AnnualAnomaly,
    AnomalyPoint,
    BoxPlotSeries,
    CalendarHeatmap,
    ClimatologyPoint,
    ComparisonRequest,
    ComparisonResponse,
    CorrelationMatrixSchema,
    DecompositionSchema,
    RateOfChangePoint,
    RegressionSchema,
    ScatterSchema,
    SeasonalPoint,
    SeriesPointSchema,
    SpatialGrid,
    SpatialPointSchema,
    SpatialRequest,
    SpatialResponse,
    StatisticsRequest,
    StatisticsResponse,
    TimeSeriesRequest,
    TimeSeriesResponse,
    TimeSeriesStats,
    VisualizableDatasetSummary,
    VizFilterParams,
)
from app.services import settings_service
from app.services.query_concurrency import bounded_to_thread

_CACHE_TTL_SECONDS = 300

# Bangladesh 4-season breakdown — matches bodp-frontend's TemporalModule.tsx
# quarters exactly (month is 1-indexed here vs. the frontend's 0-indexed).
_SEASONS: dict[str, list[int]] = {
    "Pre-Monsoon (Mar-May)": [3, 4, 5],
    "Monsoon (Jun-Sep)": [6, 7, 8, 9],
    "Post-Monsoon (Oct-Nov)": [10, 11],
    "Winter (Dec-Feb)": [12, 1, 2],
}

# Month -> (season start month, year offset) for resolution="seasonal"
# time-series bucketing (distinct from _SEASONS above, which only powers
# the single lifetime "Seasonal Pattern" summary and never groups by
# year). A Winter season spanning Dec 2024 + Jan 2025 + Feb 2025 is
# bucketed at its own START date, 2024-12-01 — so December uses offset 0
# (bucket dated the same year) while January/February use offset -1
# (bucket dated the PRIOR year, i.e. the December they belong with) — so
# each season's 3 calendar months are always contiguous in real time and
# land in the identical bucket. There is no pre-existing convention
# elsewhere in this codebase to follow here since nothing previously
# grouped seasons per-year; this establishes the one canonical definition
# every storage-tier path must now share.
_SEASON_START_MONTH: dict[int, tuple[int, int]] = {
    1: (12, -1),  # Jan -> Winter bucket dated the PRIOR December
    2: (12, -1),  # Feb -> Winter bucket dated the PRIOR December
    3: (3, 0),
    4: (3, 0),
    5: (3, 0),
    6: (6, 0),
    7: (6, 0),
    8: (6, 0),
    9: (6, 0),
    10: (10, 0),
    11: (10, 0),
    12: (12, 0),  # Dec -> Winter bucket dated THIS December
}


def season_bucket_date(d: date) -> date:
    """Maps any date to its representative Bangladesh-season bucket date
    (the 1st of the season's starting month) — the single canonical
    (year, season) grouping every resolution="seasonal" code path
    (legacy SQL, DuckDB/Parquet, Zarr/xarray) must use, so all three
    storage tiers bucket the exact same date into the exact same bucket.
    Mirrors _SEASONS' month groups exactly (Pre-Monsoon Mar-May, Monsoon
    Jun-Sep, Post-Monsoon Oct-Nov, Winter Dec-Feb)."""
    start_month, year_offset = _SEASON_START_MONTH[d.month]
    return date(d.year + year_offset, start_month, 1)


def _cache_key(module: str, params: VizFilterParams) -> str:
    payload = json.dumps(params.model_dump(mode="json"), sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"viz:{module}:{digest}"


def _apply_viz_filters(query, f: VizFilterParams):
    """Shared WHERE-clause builder for /visualize/* queries — same idiom as
    catalog_service._apply_record_filters. dataset_id (PLAN.md Phase 4)
    scopes to one dataset once the frontend's dataset selector is used;
    omitting it preserves the original cross-dataset query behavior
    (parameter/category/station cutting across every dataset) exactly,
    which is still the correct behavior for any caller that hasn't
    adopted the selector yet."""
    if f.dataset_id:
        query = query.where(DatasetRecord.dataset_id == f.dataset_id)
    if f.category:
        query = query.where(
            DatasetRecord.dataset_id.in_(
                select(Dataset.id).where(Dataset.category.has(DatasetCategory.name == f.category))
            )
        )
    if f.station:
        query = query.where(
            DatasetRecord.station_id.in_(select(Station.id).where(Station.code == f.station))
        )
    if f.date_from:
        query = query.where(DatasetRecord.time >= f.date_from)
    if f.date_to:
        query = query.where(DatasetRecord.time <= f.date_to)
    if f.depth_min is not None:
        query = query.where(DatasetRecord.depth_m >= f.depth_min)
    if f.depth_max is not None:
        query = query.where(DatasetRecord.depth_m <= f.depth_max)
    if None not in (f.lat_min, f.lat_max, f.lon_min, f.lon_max):
        envelope = func.ST_MakeEnvelope(f.lon_min, f.lat_min, f.lon_max, f.lat_max, 4326)
        query = query.where(func.ST_Intersects(DatasetRecord.geom, envelope))
    return query


# --- Storage routing (PLAN.md Phase 5) ---
#
# Mirrors catalog_service._non_legacy_dataset_files/get_filtered_records'
# additive routing pattern exactly: the legacy DatasetRecord SQL query
# always runs (untouched, below), and PARQUET/CHUNKED_ARRAY-backed files
# belonging to the SAME dataset_id are queried via tabular_query_service/
# gridded_query_service and merged in on top. Only reachable when a
# caller actually supplied dataset_id — Visualize's pre-Phase-4 global
# cross-dataset queries have no single dataset to route by file, so they
# keep querying DatasetRecord exclusively, exactly as before.


async def _non_legacy_dataset_files(db: AsyncSession, dataset_id: uuid.UUID) -> dict[str, list[DatasetFile]]:
    result = await db.execute(
        select(DatasetFile).where(
            DatasetFile.dataset_id == dataset_id,
            DatasetFile.storage_kind.in_([StorageKind.PARQUET.value, StorageKind.CHUNKED_ARRAY.value]),
        )
    )
    files = list(result.scalars().all())
    grouped: dict[str, list[DatasetFile]] = {}
    for file in files:
        grouped.setdefault(file.storage_kind, []).append(file)
    return grouped


def _viz_filter_to_records_filter(f: VizFilterParams):
    from app.services.catalog_service import RecordsFilter

    return RecordsFilter(
        date_from=f.date_from,
        date_to=f.date_to,
        lat_min=f.lat_min,
        lat_max=f.lat_max,
        lon_min=f.lon_min,
        lon_max=f.lon_max,
        depth_min=f.depth_min,
        depth_max=f.depth_max,
        station=f.station,
    )


async def _merged_timeseries(
    db: AsyncSession, dataset_id: uuid.UUID, parameter: str, f: VizFilterParams, *, resolution: str
) -> list[tuple[date, float, int]]:
    """Combines every PARQUET/CHUNKED_ARRAY file's per-bucket (avg, count)
    contributions into one correctly-weighted series — a plain mean of
    per-file bucket averages would be wrong once more than one file
    covers the same bucket, so buckets are recombined as a weighted mean
    (sum(avg*count)/sum(count)) exactly like combining two SQL AVG()
    results would require. Returns (bucket, avg, n) — n is the TOTAL
    weight backing that bucket's avg (not necessarily a real row count
    for gridded data, see below), so callers can correctly fold this
    into a further weighted merge (e.g. with the legacy SQL path's own
    per-bucket counts) instead of treating an already-averaged bucket as
    a single unweighted observation."""
    from app.services import gridded_query_service, tabular_query_service

    record_filter = _viz_filter_to_records_filter(f)
    grouped = await _non_legacy_dataset_files(db, dataset_id)

    weighted: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])  # bucket -> [sum(avg*n), sum(n)]
    for file in grouped.get(StorageKind.PARQUET.value, []):
        rows, _cols = await bounded_to_thread(
            tabular_query_service.get_timeseries_with_counts, file, parameter, record_filter, resolution=resolution
        )
        for bucket, avg, n in rows:
            weighted[bucket][0] += avg * n
            weighted[bucket][1] += n

    for file in grouped.get(StorageKind.CHUNKED_ARRAY.value, []):
        rows = await bounded_to_thread(
            gridded_query_service.get_timeseries_aggregate,
            file,
            parameter=parameter,
            resolution=resolution,
            f=record_filter,
        )
        for bucket, avg in rows:
            # Gridded aggregate has no per-bucket row count to weight by —
            # treated as a single observation, same convention
            # get_timeseries_with_counts falls back to when a bucket has
            # exactly one contributing row.
            weighted[bucket][0] += avg
            weighted[bucket][1] += 1

    return sorted(
        (bucket, total / n, int(n)) for bucket, (total, n) in weighted.items() if n > 0
    )


# --- Dataset scoping (PLAN.md Phase 4) ---


async def validate_parameter_for_dataset(
    db: AsyncSession, dataset_id: uuid.UUID, parameter: str
) -> None:
    """Rejects a parameter that isn't an admin-approved Visualization
    Variable for the given dataset — closes the gap flagged in PLAN.md
    where any string was previously silently accepted (or silently
    returned empty data). Only called when the caller actually supplied a
    dataset_id (see _apply_viz_filters' docstring) — a request with no
    dataset_id keeps the original unvalidated cross-dataset behavior."""
    result = await db.execute(
        select(func.count())
        .select_from(DatasetVariable)
        .where(
            DatasetVariable.dataset_id == dataset_id,
            DatasetVariable.name == parameter,
            DatasetVariable.roles.any("visualization_variable"),
        )
    )
    if result.scalar_one() == 0:
        raise HTTPException(
            status_code=422,
            detail=f"'{parameter}' is not an approved Visualization Variable for this dataset.",
        )


async def get_visualizable_datasets(db: AsyncSession) -> list[VisualizableDatasetSummary]:
    """Backs the Visualize module's dataset selector — only datasets an
    admin has reviewed (Phase 3) with at least one approved Visualization
    Variable ever appear, since selecting anything else would immediately
    hit validate_parameter_for_dataset's 422 for every parameter."""
    result = await db.execute(
        select(Dataset, DatasetVariable.name)
        .join(DatasetVariable, DatasetVariable.dataset_id == Dataset.id)
        .where(
            Dataset.schema_reviewed_at.is_not(None),
            DatasetVariable.roles.any("visualization_variable"),
        )
        .order_by(Dataset.title, DatasetVariable.name)
    )
    rows = result.all()

    by_dataset: dict[uuid.UUID, VisualizableDatasetSummary] = {}
    for dataset, variable_name in rows:
        if dataset.id not in by_dataset:
            by_dataset[dataset.id] = VisualizableDatasetSummary(
                id=dataset.id, code=dataset.code, title=dataset.title, variables=[]
            )
        by_dataset[dataset.id].variables.append(variable_name)

    return list(by_dataset.values())


# --- Admin-configurable compute limits ---
#
# Guards against oversized /visualize/* requests that could exhaust
# worker memory/CPU: interpolation grid size, AOI (bounding box) area,
# and date-range span. All three are read from SiteSettings (admin-
# editable via /admin/settings/visualization-limits) rather than
# hardcoded, so an admin can raise/lower them without a deploy.

_EARTH_RADIUS_KM = 6371.0


def _bbox_area_km2(lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> float:
    """Approximate area of a lat/lon rectangle in km², via a flat-earth
    approximation scaled by cos(mean latitude) for the east-west side —
    accurate to well within the margin needed for a soft admin limit at
    these scales (hundreds to low-thousands of km²), no geospatial
    dependency needed beyond stdlib math."""
    lat_span_km = (lat_max - lat_min) * (math.pi / 180.0) * _EARTH_RADIUS_KM
    mean_lat_rad = math.radians((lat_min + lat_max) / 2.0)
    lon_span_km = (lon_max - lon_min) * (math.pi / 180.0) * _EARTH_RADIUS_KM * math.cos(mean_lat_rad)
    return abs(lat_span_km * lon_span_km)


def _check_date_range_limit(
    settings_row: SiteSettings, f: VizFilterParams, *, max_days: int | None
) -> None:
    if max_days is None or f.date_from is None or f.date_to is None:
        return
    span_days = (f.date_to - f.date_from).days
    if span_days > max_days:
        raise HTTPException(
            status_code=422,
            detail=f"Requested date range ({span_days} days) exceeds the configured maximum "
            f"of {max_days} days for this module. Narrow the date range and try again.",
        )


def _check_spatial_limits(settings_row: SiteSettings, params: SpatialRequest) -> None:
    max_resolution = settings_row.viz_max_grid_resolution
    if max_resolution is not None and params.grid_resolution > max_resolution:
        raise HTTPException(
            status_code=422,
            detail=f"Requested grid resolution ({params.grid_resolution}) exceeds the "
            f"configured maximum of {max_resolution}.",
        )

    max_aoi_km2 = settings_row.viz_max_aoi_km2
    if max_aoi_km2 is not None:
        area_km2 = _bbox_area_km2(
            params.bounds.lat_min, params.bounds.lat_max, params.bounds.lon_min, params.bounds.lon_max
        )
        if area_km2 > max_aoi_km2:
            raise HTTPException(
                status_code=422,
                detail=f"Requested area of interest (~{area_km2:.0f} km²) exceeds the configured "
                f"maximum of {max_aoi_km2:.0f} km². Draw a smaller area and try again.",
            )

    _check_date_range_limit(settings_row, params, max_days=settings_row.viz_max_date_range_days_spatial)


# --- Shared math helpers (ported 1:1 from bodp-frontend/src/lib/mock-data/stats.ts) ---


def _mean(xs: list[float]) -> float:
    return float(np.mean(xs)) if xs else 0.0


def _median(xs: list[float]) -> float:
    return float(np.median(xs)) if xs else 0.0


def _std_dev(xs: list[float]) -> float:
    # Sample std dev (n-1), matching the frontend's stdDev() exactly.
    return float(np.std(xs, ddof=1)) if len(xs) >= 2 else 0.0


def _linreg(dates: list[date], ys: list[float]) -> tuple[float, float]:
    """Least-squares linear regression against real elapsed time, in
    fractional years since dates[0] — NOT bucket index. Index-based
    regression only produces a physically meaningful "per year" slope
    when every bucket happens to be exactly one calendar month
    (resolution="monthly"); at any other resolution, or whenever a
    bucket is missing (gaps are dropped from the series entirely, not
    padded), an index-based slope silently means something other than
    "value change per year". Regressing against true elapsed time makes
    the resulting slope unconditionally "value change per year"
    regardless of resolution or gaps, so trend_per_year needs no
    resolution-dependent multiplier at the call site."""
    n = len(ys)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    xs = [(d - dates[0]).days / 365.25 for d in dates]
    mx, my = _mean(xs), _mean(ys)
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    slope = 0.0 if den == 0 else num / den
    intercept = my - slope * mx
    return slope, intercept


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mx, my = _mean(xs), _mean(ys)
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = sum((xs[i] - mx) ** 2 for i in range(n))
    dy = sum((ys[i] - my) ** 2 for i in range(n))
    den = (dx * dy) ** 0.5
    return 0.0 if den == 0 else num / den


def _resolution_trunc(resolution: str):
    """SQL date_trunc part name for every resolution EXCEPT "seasonal" —
    Postgres has no native trunc unit for the app's Bangladesh 4-season
    definition (its 'quarter' is an ordinary calendar quarter, Jan-Mar/
    Apr-Jun/Jul-Sep/Oct-Dec, not Pre-Monsoon/Monsoon/Post-Monsoon/Winter).
    Callers must special-case "seasonal" themselves, bucketing via
    season_bucket_date() over day-level rows instead of using this
    function's output for that case — see get_timeseries."""
    return {
        "daily": "day",
        "monthly": "month",
        "annual": "year",
    }[resolution]


# --- Time series (task 1) ---


async def get_timeseries(db: AsyncSession, params: TimeSeriesRequest) -> TimeSeriesResponse:
    if params.dataset_id:
        await validate_parameter_for_dataset(db, params.dataset_id, params.parameter)

    settings_row = await settings_service.get_settings(db)
    _check_date_range_limit(
        settings_row, params, max_days=settings_row.viz_max_date_range_days_timeseries
    )

    cache_key = _cache_key("timeseries", params)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return TimeSeriesResponse.model_validate(cached)

    # PLAN.md Phase 5: sql_weighted starts from the legacy SQL buckets
    # (always queried, exactly as before), then PARQUET/CHUNKED_ARRAY
    # files belonging to the SAME dataset_id are merged in additively —
    # see _merged_timeseries' docstring for why this is a weighted mean,
    # not a plain concatenation.
    weighted: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])

    if params.resolution == "seasonal":
        # No native SQL trunc unit for the Bangladesh 4-season definition
        # (season_bucket_date's docstring) — group by real day-level rows
        # in SQL, then re-bucket into (year, season) in Python.
        bucket = DatasetRecord.time.label("bucket")
        query = (
            select(bucket, func.avg(DatasetRecord.value).label("avg_value"), func.count().label("n"))
            .where(DatasetRecord.parameter == params.parameter)
            .group_by(bucket)
            .order_by(bucket)
        )
        query = _apply_viz_filters(query, params)
        result = await db.execute(query)
        for row in result.all():
            season_date = season_bucket_date(row.bucket)
            weighted[season_date][0] += float(row.avg_value) * row.n
            weighted[season_date][1] += row.n
    else:
        trunc_unit = _resolution_trunc(params.resolution)
        bucket = func.date_trunc(trunc_unit, DatasetRecord.time).label("bucket")
        query = (
            select(bucket, func.avg(DatasetRecord.value).label("avg_value"), func.count().label("n"))
            .where(DatasetRecord.parameter == params.parameter)
            .group_by(bucket)
            .order_by(bucket)
        )
        query = _apply_viz_filters(query, params)
        result = await db.execute(query)
        for row in result.all():
            bucket_date = row.bucket.date()
            weighted[bucket_date][0] += float(row.avg_value) * row.n
            weighted[bucket_date][1] += row.n

    if params.dataset_id:
        non_legacy_rows = await _merged_timeseries(
            db, params.dataset_id, params.parameter, params, resolution=params.resolution
        )
        # _merged_timeseries returns (bucket, avg, n) — n is the real
        # weight backing that average, so it must be folded into the SQL
        # path's own weighted totals the same way (sum(avg*n), sum(n)),
        # not added as a single unweighted point, or a bucket spanning
        # both a legacy row_records row and many Parquet rows would be
        # incorrectly skewed toward whichever source happened to average
        # fewer contributing rows.
        for bucket_date, avg, n in non_legacy_rows:
            weighted[bucket_date][0] += avg * n
            weighted[bucket_date][1] += n

    series = [
        SeriesPointSchema(date=bucket_date, value=total / n)
        for bucket_date, (total, n) in sorted(weighted.items())
        if n > 0
    ]
    values = [p.value for p in series]
    series_dates = [p.date for p in series]

    slope, intercept = _linreg(series_dates, values)
    stats = TimeSeriesStats(
        mean=_mean(values),
        median=_median(values),
        std=_std_dev(values),
        min=min(values) if values else 0.0,
        max=max(values) if values else 0.0,
        # slope is already "value change per year" (regression is against
        # real elapsed time in fractional years, not bucket index) — no
        # resolution-dependent multiplier needed, unlike the old
        # index-based regression which only meant "per year" at
        # resolution="monthly".
        trend_per_year=slope,
        count=len(values),
    )

    window = 3
    moving_average = [
        _mean(values[max(0, i - window + 1) : i + 1]) for i in range(len(values))
    ]
    trend_line = [
        intercept + slope * ((d - series_dates[0]).days / 365.25) for d in series_dates
    ]

    seasonal = [
        SeasonalPoint(label=label, value=_mean([p.value for p in series if p.date.month in months]))
        for label, months in _SEASONS.items()
    ]

    by_month: dict[int, list[float]] = defaultdict(list)
    for p in series:
        by_month[p.date.month].append(p.value)
    climatology = [
        ClimatologyPoint(month=month_abbr[m], value=_mean(by_month.get(m, [])))
        for m in range(1, 13)
    ]

    rate_of_change = [
        RateOfChangePoint(date=series[i].date, delta=values[i] - values[i - 1])
        for i in range(1, len(series))
    ]

    anomaly = [
        AnomalyPoint(date=p.date, anomaly=p.value - stats.mean) for p in series
    ]

    response = TimeSeriesResponse(
        series=series,
        stats=stats,
        moving_average=moving_average,
        trend_line=trend_line,
        seasonal=seasonal,
        climatology=climatology,
        rate_of_change=rate_of_change,
        anomaly=anomaly,
    )
    await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
    return response


# --- Spatial (task 2) ---


async def get_spatial_points(db: AsyncSession, params: SpatialRequest) -> list[SpatialPointSchema]:
    """Latest value per station (mirrors the prototype's stationLatestValue())
    within the requested filters."""
    latest_per_station = (
        select(
            DatasetRecord.station_id,
            func.max(DatasetRecord.time).label("max_time"),
        )
        .where(DatasetRecord.parameter == params.parameter, DatasetRecord.station_id.is_not(None))
        .group_by(DatasetRecord.station_id)
    )
    latest_per_station = _apply_viz_filters(latest_per_station, params)
    latest_subq = latest_per_station.subquery()

    query = (
        select(Station.code, Station.name, Station.lat, Station.lon, DatasetRecord.value)
        .join(latest_subq, latest_subq.c.station_id == Station.id)
        .join(
            DatasetRecord,
            (DatasetRecord.station_id == latest_subq.c.station_id)
            & (DatasetRecord.time == latest_subq.c.max_time)
            & (DatasetRecord.parameter == params.parameter),
        )
    )
    result = await db.execute(query)
    rows = result.all()
    points = [
        SpatialPointSchema(station=row.name, lat=float(row.lat), lon=float(row.lon), value=float(row.value))
        for row in rows
    ]

    if params.dataset_id:
        from app.services import gridded_query_service, tabular_query_service

        record_filter = _viz_filter_to_records_filter(params)
        grouped = await _non_legacy_dataset_files(db, params.dataset_id)
        for file in grouped.get(StorageKind.PARQUET.value, []):
            file_points = await bounded_to_thread(
                tabular_query_service.get_spatial_points, file, params.parameter, record_filter
            )
            points.extend(
                SpatialPointSchema(station=p.station, lat=p.lat, lon=p.lon, value=p.value) for p in file_points
            )
        for file in grouped.get(StorageKind.CHUNKED_ARRAY.value, []):
            file_points = await bounded_to_thread(
                gridded_query_service.get_spatial_points, file, params.parameter, record_filter
            )
            points.extend(
                SpatialPointSchema(station=p.station, lat=p.lat, lon=p.lon, value=p.value) for p in file_points
            )

    return points


def _idw(points: list[SpatialPointSchema], lats: np.ndarray, lons: np.ndarray, power: int = 2) -> np.ndarray:
    """Vectorized inverse-distance-weighting onto a regular grid — same
    algorithm as bodp-frontend/src/lib/mock-data/stats.ts's idwGrid()."""
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    z = np.zeros_like(lat_grid, dtype=float)
    w_sum = np.zeros_like(lat_grid, dtype=float)
    for p in points:
        d = np.hypot(lat_grid - p.lat, lon_grid - p.lon)
        d = np.where(d == 0, 1e-6, d)
        w = 1.0 / (d**power)
        z += w * p.value
        w_sum += w
    return np.divide(z, w_sum, out=np.zeros_like(z), where=w_sum != 0)


def _nearest(points: list[SpatialPointSchema], lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    coords = np.array([[p.lat, p.lon] for p in points])
    values = np.array([p.value for p in points])
    interpolator = NearestNDInterpolator(coords, values)
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    return interpolator(lat_grid, lon_grid)


def compute_interpolation(
    points: list[SpatialPointSchema], params: SpatialRequest
) -> tuple[SpatialGrid, str]:
    lats = np.linspace(params.bounds.lat_min, params.bounds.lat_max, params.grid_resolution)
    lons = np.linspace(params.bounds.lon_min, params.bounds.lon_max, params.grid_resolution)

    if params.method == "nearest":
        z = _nearest(points, lats, lons)
        method_used = "nearest"
    else:
        # "kriging" silently falls back to IDW, matching the prototype's
        # own behavior exactly (Master Plan §3 Phase 7 kickoff decision —
        # no pykrige dependency added).
        z = _idw(points, lats, lons)
        method_used = "idw"

    grid = SpatialGrid(lats=lats.tolist(), lons=lons.tolist(), z=z.tolist())
    return grid, method_used


async def create_spatial_request(db: AsyncSession, params: SpatialRequest) -> SpatialResponse:
    """Decides sync-vs-Celery-job the same way extraction_service.create_extraction
    does for extractions — light requests (few points, small grid) compute
    in-process; heavy ones dispatch to Celery (Master Plan §3 Phase 7 task 2)."""
    if params.dataset_id:
        await validate_parameter_for_dataset(db, params.dataset_id, params.parameter)

    settings_row = await settings_service.get_settings(db)
    # A correctness gate, not a performance heuristic — runs before the
    # sync/async work-unit decision so an oversized request is rejected
    # regardless of which path it would otherwise take.
    _check_spatial_limits(settings_row, params)

    points = await get_spatial_points(db, params)
    # Work scales with grid cells × source points (every point contributes
    # to every cell in IDW/NN) — this product is the real cost driver, not
    # either factor alone. At the prototype's own resolution options
    # (20/40/60 = low/medium/high) and typical station counts (~20-30),
    # only "high" with many stations should ever cross the threshold.
    work_units = (params.grid_resolution**2) * max(1, len(points))

    if work_units <= settings.VIZ_SPATIAL_SYNC_THRESHOLD_CELLS:
        grid, method_used = compute_interpolation(points, params)
        return SpatialResponse(status="complete", points=points, grid=grid, method_used=method_used)

    job = VisualizationJob(
        job_type="spatial_interpolation",
        params=params.model_dump(mode="json"),
        status=VizJobStatus.QUEUED.value,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    from app.worker.tasks.visualize import run_spatial_interpolation

    task = run_spatial_interpolation.delay(str(job.id))
    job.celery_task_id = task.id
    await db.commit()

    return SpatialResponse(status="queued", job_id=job.id)


async def get_spatial_job(db: AsyncSession, job_id: uuid.UUID) -> VisualizationJob:
    job = await db.get(VisualizationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Visualization job not found")
    return job


# --- Comparison (task 3) ---


async def get_comparison(db: AsyncSession, params: ComparisonRequest) -> ComparisonResponse:
    if params.dataset_id:
        for parameter in params.parameters:
            await validate_parameter_for_dataset(db, params.dataset_id, parameter)

    settings_row = await settings_service.get_settings(db)
    _check_date_range_limit(
        settings_row, params, max_days=settings_row.viz_max_date_range_days_comparison
    )

    cache_key = _cache_key("comparison", params)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return ComparisonResponse.model_validate(cached)

    non_legacy_grouped = (
        await _non_legacy_dataset_files(db, params.dataset_id) if params.dataset_id else {}
    )
    record_filter = _viz_filter_to_records_filter(params) if params.dataset_id else None

    series_by_parameter: dict[str, list[SeriesPointSchema]] = {}
    for parameter in params.parameters:
        query = (
            select(DatasetRecord.time, func.avg(DatasetRecord.value).label("avg_value"))
            .where(DatasetRecord.parameter == parameter)
            .group_by(DatasetRecord.time)
            .order_by(DatasetRecord.time)
        )
        query = _apply_viz_filters(query, params)
        result = await db.execute(query)

        by_date: dict[date, list[float]] = defaultdict(list)
        for row in result.all():
            by_date[row.time].append(float(row.avg_value))

        if non_legacy_grouped:
            from app.services import gridded_query_service, tabular_query_service

            for file in non_legacy_grouped.get(StorageKind.PARQUET.value, []):
                raw = await bounded_to_thread(
                    tabular_query_service.get_raw_values, file, parameter, record_filter
                )
                for _station, t, value in raw:
                    if t is not None:
                        by_date[t].append(value)
            for file in non_legacy_grouped.get(StorageKind.CHUNKED_ARRAY.value, []):
                raw = await bounded_to_thread(
                    gridded_query_service.get_raw_values, file, parameter, record_filter
                )
                for _station, t, value in raw:
                    if t is not None:
                        by_date[t].append(value)

        series_by_parameter[parameter] = [
            SeriesPointSchema(date=d, value=_mean(vals)) for d, vals in sorted(by_date.items())
        ]

    x_param, y_param = params.parameters[0], params.parameters[1]
    x_by_date = {p.date: p.value for p in series_by_parameter[x_param]}
    y_by_date = {p.date: p.value for p in series_by_parameter[y_param]}
    common_dates = sorted(set(x_by_date) & set(y_by_date))
    x_vals = [x_by_date[d] for d in common_dates]
    y_vals = [y_by_date[d] for d in common_dates]

    r = _pearson(x_vals, y_vals)
    slope, intercept = _linreg_xy(x_vals, y_vals)

    scatter = ScatterSchema(
        x_parameter=x_param,
        y_parameter=y_param,
        x=x_vals,
        y=y_vals,
        r=r,
        regression=RegressionSchema(slope=slope, intercept=intercept),
        # Paired-sample transparency (Visualize Phase A) — x_vals/y_vals
        # are joined by exact calendar-date match (common_dates above); no
        # tolerance window. A single date matching between two different-
        # resolution parameters could paired-correlate a small fraction of
        # either series' real observations, so the count backing r is
        # surfaced explicitly rather than left for the caller to infer.
        n=len(common_dates),
        pairing_method="exact_date_match",
    )

    n = len(params.parameters)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
                continue
            pi, pj = params.parameters[i], params.parameters[j]
            by_i = {p.date: p.value for p in series_by_parameter[pi]}
            by_j = {p.date: p.value for p in series_by_parameter[pj]}
            common = sorted(set(by_i) & set(by_j))
            matrix[i][j] = _pearson([by_i[d] for d in common], [by_j[d] for d in common])

    response = ComparisonResponse(
        scatter=scatter,
        series_by_parameter=series_by_parameter,
        correlation_matrix=CorrelationMatrixSchema(parameters=params.parameters, matrix=matrix),
    )
    await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
    return response


def _linreg_xy(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Regression of y against real x values (not index) — used for the
    scatter plot's regression line, plotted against sorted x values per
    the prototype's explicit bug-fix comment in ComparisonModule.tsx."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mx, my = _mean(xs), _mean(ys)
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    slope = 0.0 if den == 0 else num / den
    intercept = my - slope * mx
    return slope, intercept


# --- Statistics (task 4) ---


async def get_statistics(db: AsyncSession, params: StatisticsRequest) -> StatisticsResponse:
    if params.dataset_id:
        await validate_parameter_for_dataset(db, params.dataset_id, params.parameter)

    settings_row = await settings_service.get_settings(db)
    _check_date_range_limit(
        settings_row, params, max_days=settings_row.viz_max_date_range_days_statistics
    )

    cache_key = _cache_key("statistics", params)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return StatisticsResponse.model_validate(cached)

    non_legacy_grouped = (
        await _non_legacy_dataset_files(db, params.dataset_id) if params.dataset_id else {}
    )
    record_filter = _viz_filter_to_records_filter(params) if params.dataset_id else None

    box_query = (
        select(Station.name, DatasetRecord.value)
        .join(Station, Station.id == DatasetRecord.station_id)
        .where(DatasetRecord.parameter == params.parameter)
        .order_by(Station.name)
    )
    box_query = _apply_viz_filters(box_query, params)
    box_result = await db.execute(box_query)
    by_station: dict[str, list[float]] = defaultdict(list)
    for row in box_result.all():
        by_station[row.name].append(float(row.value))

    if non_legacy_grouped:
        from app.services import gridded_query_service, tabular_query_service

        for file in non_legacy_grouped.get(StorageKind.PARQUET.value, []):
            raw = await bounded_to_thread(
                tabular_query_service.get_raw_values, file, params.parameter, record_filter
            )
            for station, _t, value in raw:
                by_station[station or f"file:{file.id}"].append(value)
        for file in non_legacy_grouped.get(StorageKind.CHUNKED_ARRAY.value, []):
            raw = await bounded_to_thread(
                gridded_query_service.get_raw_values, file, params.parameter, record_filter
            )
            for station, _t, value in raw:
                by_station[station or f"file:{file.id}"].append(value)

    station_names = sorted(by_station)[:6]
    box_plot = [BoxPlotSeries(station=name, values=by_station[name]) for name in station_names]

    # Series construction (feeds histogram/annual_anomalies/decomposition/
    # calendar_heatmap below) reuses the exact same count-weighted merge
    # _merged_timeseries provides for get_timeseries, rather than a
    # separate ad hoc aggregation — folding raw Parquet/Zarr rows onto the
    # same list as an already-averaged legacy SQL bucket (the previous
    # approach here) treats one pre-averaged value as equally weighted
    # against however many raw rows happen to exist for that date, which
    # is exactly the bug pattern _merged_timeseries' docstring warns
    # against. Both modules must agree on one canonical aggregation so the
    # same dataset/filters can't report different numbers depending which
    # endpoint is queried. resolution="daily" preserves this endpoint's
    # existing per-calendar-day granularity (it never bucketed coarser
    # than a single DatasetRecord.time value).
    series_query = (
        select(DatasetRecord.time, func.avg(DatasetRecord.value).label("avg_value"), func.count().label("n"))
        .where(DatasetRecord.parameter == params.parameter)
        .group_by(DatasetRecord.time)
        .order_by(DatasetRecord.time)
    )
    series_query = _apply_viz_filters(series_query, params)
    series_result = await db.execute(series_query)
    weighted: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in series_result.all():
        weighted[row.time][0] += float(row.avg_value) * row.n
        weighted[row.time][1] += row.n

    if params.dataset_id:
        non_legacy_rows = await _merged_timeseries(
            db, params.dataset_id, params.parameter, params, resolution="daily"
        )
        for bucket_date, avg, n in non_legacy_rows:
            weighted[bucket_date][0] += avg * n
            weighted[bucket_date][1] += n

    series = [
        (bucket_date, total / n) for bucket_date, (total, n) in sorted(weighted.items()) if n > 0
    ]
    values = [v for _, v in series]

    histogram = values

    by_year: dict[int, list[float]] = defaultdict(list)
    for d, v in series:
        by_year[d.year].append(v)
    overall_mean = _mean(values)
    annual_anomalies = [
        AnnualAnomaly(year=year, anomaly=_mean(vals) - overall_mean)
        for year, vals in sorted(by_year.items())
    ]

    decomposition = _classical_decomposition(series)

    years = sorted(by_year)
    months = [month_abbr[m] for m in range(1, 13)]
    by_year_month: dict[tuple[int, int], list[float]] = defaultdict(list)
    for d, v in series:
        by_year_month[(d.year, d.month)].append(v)
    z = [[_mean(by_year_month.get((y, m), [])) for m in range(1, 13)] for y in years]

    response = StatisticsResponse(
        box_plot=box_plot,
        histogram=histogram,
        annual_anomalies=annual_anomalies,
        decomposition=decomposition,
        calendar_heatmap=CalendarHeatmap(years=years, months=months, z=z),
    )
    await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
    return response


def _classical_decomposition(series: list[tuple[date, float]]) -> DecompositionSchema:
    """Real classical additive decomposition — moving-average trend (6-month
    centered window, matching the prototype's window size) + per-month
    seasonal index + residual. Replaces the prototype's made-up
    `residual = value - trend - seasonal*0.3` formula with
    trend + seasonal + residual == value exactly, by construction."""
    dates = [d for d, _ in series]
    values = np.array([v for _, v in series], dtype=float)
    n = len(values)

    window = 6
    trend = np.full(n, np.nan)
    half = window // 2
    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half + 1)
        trend[i] = np.mean(values[start:end])

    detrended = values - trend
    seasonal_by_month: dict[int, list[float]] = defaultdict(list)
    for d, val in zip(dates, detrended):
        if not np.isnan(val):
            seasonal_by_month[d.month].append(val)
    seasonal_index = {m: float(np.mean(vals)) if vals else 0.0 for m, vals in seasonal_by_month.items()}
    seasonal = np.array([seasonal_index.get(d.month, 0.0) for d in dates])

    residual = values - trend - seasonal

    return DecompositionSchema(
        dates=dates,
        trend=[float(x) for x in trend],
        seasonal=[float(x) for x in seasonal],
        residual=[float(x) for x in residual],
    )


# --- Stations (supports useVizFilters' filteredStations) ---


async def get_filtered_stations(db: AsyncSession, params: VizFilterParams) -> list[Station]:
    query = select(Station).distinct().order_by(Station.name)
    if None not in (params.lat_min, params.lat_max):
        query = query.where(Station.lat >= params.lat_min, Station.lat <= params.lat_max)
    if None not in (params.lon_min, params.lon_max):
        query = query.where(Station.lon >= params.lon_min, Station.lon <= params.lon_max)
    if params.depth_min is not None:
        query = query.where(Station.depth_m >= params.depth_min)
    if params.depth_max is not None:
        query = query.where(Station.depth_m <= params.depth_max)
    result = await db.execute(query)
    return list(result.scalars().all())
