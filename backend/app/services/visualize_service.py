import asyncio
import hashlib
import json
import math
import uuid
from calendar import month_abbr
from collections import defaultdict
from datetime import date
from typing import Literal

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
    VariableDataType,
)
from app.models.visualize import VisualizationJob, VizJobStatus
from app.schemas.catalog import SpatialBBox
from app.schemas.visualize import (
    AnnualAnomaly,
    AnomalyPoint,
    BoxPlotSeries,
    CalendarHeatmap,
    ClimatologyPoint,
    ComparisonJobResponse,
    ComparisonRequest,
    ComparisonResponse,
    CorrelationMatrixSchema,
    CoverageRequest,
    CoverageResponse,
    DecompositionSchema,
    ProfilePointSchema,
    ProfilesRequest,
    ProfilesResponse,
    RateOfChangePoint,
    RegressionSchema,
    ScatterSchema,
    SeasonalPoint,
    SeriesPointSchema,
    SpatialGrid,
    SpatialPointSchema,
    SpatialRequest,
    SpatialResponse,
    StationProfileSchema,
    StatisticsJobResponse,
    StatisticsRequest,
    StatisticsResponse,
    TimeSeriesRequest,
    TimeSeriesResponse,
    TimeSeriesStats,
    TSPairSchema,
    VisualizableDatasetSummary,
    VizFilterParams,
)
from app.services import settings_service
from app.services.query_concurrency import bounded_to_thread

_CACHE_TTL_SECONDS = 300

# Oceanographic Profiles module: kept in sync with every parser's own
# depth-alias list (csv_parser._DEPTH_ALIASES, mat_parser._DEPTH_NAMES,
# netcdf_parser._DEPTH_NAMES) plus the canonical "depth_m" DatasetRecord/
# DuckDB column name itself — dataset_has_depth_dimension matches a
# registered DatasetVariable's name against this set (lowercased) rather
# than importing each parser module here.
_DEPTH_COLUMN_NAMES = frozenset(
    {"depth", "depth_m", "depth_meters", "pressure_depth", "z", "level", "pressure"}
)

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
    db: AsyncSession, dataset_id: uuid.UUID, parameter: str, f: VizFilterParams, *,
    resolution: str, include_gridded: bool = True,
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
    a single unweighted observation.

    include_gridded=False skips the CHUNKED_ARRAY branch entirely — for
    a caller (get_statistics, Performance & Behavior investigation Phase
    6) that already fetched this same file/parameter's raw values via
    gridded_query_service.get_raw_values for its own purposes (box_plot)
    and derives its gridded daily series from that same fetch instead,
    rather than opening and reducing the same Zarr file a second time."""
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

    if include_gridded:
        for file in grouped.get(StorageKind.CHUNKED_ARRAY.value, []):
            rows = await bounded_to_thread(
                gridded_query_service.get_timeseries_aggregate,
                file,
                parameter=parameter,
                resolution=resolution,
                f=record_filter,
            )
            for bucket, avg in rows:
                # Gridded aggregate has no per-bucket row count to weight
                # by — treated as a single observation, same convention
                # get_timeseries_with_counts falls back to when a bucket
                # has exactly one contributing row.
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
            # is_dimension=True is set exclusively for lat/lon/time
            # coordinate columns (worker/tasks/ingestion.py's
            # _write_variable_registry — the only place that ever sets
            # it), never for a genuine data variable. A coordinate can
            # end up with the visualization_variable role too (an admin
            # review-UI mistake, confirmed via direct inspection of real
            # dev data — several real datasets had exactly this), and
            # since the query layer correctly never melts a coordinate
            # column into a "parameter" row (tabular_query_service.py's
            # coord_names exclusion; a gridded dataset's lat/lon/time are
            # xarray coordinates, not data_vars, so they're never
            # queryable as a parameter there either), accepting it here
            # would validate a parameter guaranteed to return zero data
            # — exactly the "parameter selected -> no plot" failure mode
            # this investigation was asked to fix, not a new one.
            DatasetVariable.is_dimension.is_(False),
        )
    )
    if result.scalar_one() == 0:
        raise HTTPException(
            status_code=422,
            detail=f"'{parameter}' is not an approved Visualization Variable for this dataset.",
        )


async def dataset_has_depth_dimension(db: AsyncSession, dataset_id: uuid.UUID) -> bool:
    """Cheap existence check for the Oceanographic Profiles module —
    mirrors dataset_has_temporal_dimension exactly, but for depth: a
    DatasetVariable row registered as is_dimension=True whose name matches
    one of the depth aliases the parsers detect (see csv_parser.py's
    _DEPTH_ALIASES / netcdf_parser.py's _DEPTH_NAMES / mat_parser.py's
    _DEPTH_NAMES — kept in sync here since no VERTICAL_DIMENSION role was
    added to avoid a schema migration for this feature). A depth-less
    dataset (e.g. a plain surface time series) is a legitimate, correct
    state, not missing data — surfaced via ProfilesResponse.has_depth_data
    so the frontend can show an informative message instead of an
    ambiguous empty chart."""
    result = await db.execute(
        select(func.count())
        .select_from(DatasetVariable)
        .where(
            DatasetVariable.dataset_id == dataset_id,
            DatasetVariable.is_dimension.is_(True),
            func.lower(DatasetVariable.name).in_(_DEPTH_COLUMN_NAMES),
        )
    )
    return result.scalar_one() > 0


async def dataset_depth_convention(
    db: AsyncSession, dataset_id: uuid.UUID
) -> Literal["assumed_positive_down", "assumed_negative_up"] | None:
    """Reads the depth_convention each contributing DatasetFile's parser
    already computed at ingestion time (file_metadata["depth_convention"])
    — never re-derived or guessed here. A dataset can have multiple files;
    the first non-null convention found is used (in practice every file
    belonging to one coherent oceanographic dataset shares the same
    convention — a dataset mixing both would be a genuine data-quality
    issue outside this function's scope to detect)."""
    result = await db.execute(
        select(DatasetFile.file_metadata).where(DatasetFile.dataset_id == dataset_id)
    )
    for (file_metadata,) in result.all():
        convention = (file_metadata or {}).get("depth_convention")
        if convention:
            return convention
    return None


async def dataset_has_temporal_dimension(db: AsyncSession, dataset_id: uuid.UUID) -> bool:
    """Cheap existence check (no data scan) — does this dataset have a
    detected temporal dimension at all? Not every scientific dataset has
    one (e.g. a static scattered lat/lon survey with no time axis), and
    that is a legitimate, correct state, not missing data. Distinguishes
    "no time dimension" from "a time dimension exists but the current
    filters happen to match nothing" so Temporal/Statistics/Comparison
    can tell the user which situation they're in instead of returning an
    ambiguous empty response (or, before this fix, crashing outright —
    see get_timeseries/get_statistics/get_comparison's NULL-bucket
    guards)."""
    result = await db.execute(
        select(func.count())
        .select_from(DatasetVariable)
        .where(
            DatasetVariable.dataset_id == dataset_id,
            DatasetVariable.data_type == VariableDataType.TEMPORAL.value,
        )
    )
    return result.scalar_one() > 0


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
            # See validate_parameter_for_dataset's matching comment —
            # a coordinate column (lat/lon/time) is never a real,
            # queryable parameter regardless of its approved roles, so
            # it must not appear in the dropdown this list backs either
            # (confirmed via direct inspection: several real datasets'
            # lat/lon/time columns carry visualization_variable too).
            DatasetVariable.is_dimension.is_(False),
        )
        .order_by(Dataset.title, DatasetVariable.name)
    )
    rows = result.all()

    by_dataset: dict[uuid.UUID, VisualizableDatasetSummary] = {}
    for dataset, variable_name in rows:
        if dataset.id not in by_dataset:
            by_dataset[dataset.id] = VisualizableDatasetSummary(
                id=dataset.id, code=dataset.code, title=dataset.title, variables=[],
                temporal_start=None, temporal_end=None,
            )
        by_dataset[dataset.id].variables.append(variable_name)

    if by_dataset:
        # One grouped aggregate for every listed dataset's temporal
        # extent (see catalog_service.get_dataset_temporal_extent's
        # docstring for why DatasetFile, not Dataset, is the reliable
        # source) — a single query regardless of how many datasets are
        # listed, not one per dataset.
        extent_rows = await db.execute(
            select(
                DatasetFile.dataset_id,
                func.min(DatasetFile.temporal_start),
                func.max(DatasetFile.temporal_end),
            )
            .where(
                DatasetFile.dataset_id.in_(by_dataset.keys()),
                DatasetFile.temporal_start.is_not(None),
            )
            .group_by(DatasetFile.dataset_id)
        )
        for dataset_id, temporal_start, temporal_end in extent_rows.all():
            by_dataset[dataset_id].temporal_start = temporal_start
            by_dataset[dataset_id].temporal_end = temporal_end

        # Fallback (same reasoning as catalog_service.get_dataset_
        # temporal_extent's own fallback): pre-Phase-2 legacy datasets
        # have real dated DatasetRecord rows but no DatasetFile.
        # temporal_start/end at all — one more batched, indexed
        # aggregate for whichever datasets are still unresolved, not one
        # query per dataset.
        still_unresolved = [d.id for d in by_dataset.values() if d.temporal_start is None]
        if still_unresolved:
            fallback_rows = await db.execute(
                select(
                    DatasetRecord.dataset_id,
                    func.min(DatasetRecord.time),
                    func.max(DatasetRecord.time),
                )
                .where(
                    DatasetRecord.dataset_id.in_(still_unresolved),
                    DatasetRecord.time.is_not(None),
                )
                .group_by(DatasetRecord.dataset_id)
            )
            for dataset_id, temporal_start, temporal_end in fallback_rows.all():
                by_dataset[dataset_id].temporal_start = temporal_start
                by_dataset[dataset_id].temporal_end = temporal_end

        # Same batched, no-scan pattern as the temporal extent above —
        # one grouped query for every listed dataset's spatial extent,
        # not one per dataset. Unlike temporal_start/end, Dataset.
        # spatial_extent IS reliably set by real ingestion (confirmed by
        # reading worker/tasks/ingestion.py's dataset-level union-merge
        # logic and cross-checked against real dev data directly), so no
        # DatasetFile-level fallback is needed here.
        spatial_rows = await db.execute(
            select(
                Dataset.id,
                func.ST_XMin(Dataset.spatial_extent),
                func.ST_XMax(Dataset.spatial_extent),
                func.ST_YMin(Dataset.spatial_extent),
                func.ST_YMax(Dataset.spatial_extent),
            )
            .where(Dataset.id.in_(by_dataset.keys()), Dataset.spatial_extent.is_not(None))
        )
        for dataset_id, lon_min, lon_max, lat_min, lat_max in spatial_rows.all():
            by_dataset[dataset_id].spatial_extent = SpatialBBox(
                lat_min=lat_min, lat_max=lat_max, lon_min=lon_min, lon_max=lon_max
            )

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


async def _snapshot_if_default_shape(db: AsyncSession, params: VizFilterParams) -> tuple[object, dict] | None:
    """Dataset Default-View Snapshot feature: shared extent-match check
    used by every Visualize endpoint that has a precomputed snapshot
    section (get_timeseries' default_chart, get_statistics' default_
    statistics). The Visualize page's own initial/auto-selected state is
    NOT a bare request — useVizFilters.ts's selectDataset() auto-fills
    dateFrom/dateTo/latMin/latMax/lonMin/lonMax from the newly-selected
    dataset's own real extent as soon as it's chosen (confirmed by reading
    that hook directly), so a strict "every filter field is None" check
    never matches even on first paint — the exact same gap fixed in
    catalog.py's records endpoint. This instead treats a date/bbox range
    matching the dataset's own real extent as equivalent to "no
    restriction" (category/station/depth must still be genuinely unset —
    a user cannot produce those without deliberately picking a value).

    Returns (dataset, snapshot_dict) when the request shape matches AND a
    fresh snapshot exists, else None — callers still need to check their
    own specific snapshot section (e.g. default_chart vs default_
    statistics) and its stored parameter before using it; this only
    establishes "the filter shape is the default one for the dataset's
    current snapshot", not "this specific section is present/relevant"."""
    no_non_extent_filters = (
        params.dataset_id is not None
        and params.resolution == "monthly"
        and params.category is None
        and params.station is None
        and params.depth_min is None
        and params.depth_max is None
    )
    if not no_non_extent_filters:
        return None

    from app.models.catalog import Dataset as _Dataset
    from app.services.catalog_service import get_dataset_snapshot
    from app.services.catalog_service import get_dataset_temporal_extent as _get_dataset_temporal_extent

    dataset = await db.get(_Dataset, params.dataset_id)
    if dataset is None:
        return None

    extent_temporal_start, extent_temporal_end = await _get_dataset_temporal_extent(db, params.dataset_id)
    date_matches_extent = (params.date_from is None or params.date_from == extent_temporal_start) and (
        params.date_to is None or params.date_to == extent_temporal_end
    )

    bbox_matches_extent = True
    if any([params.lat_min, params.lat_max, params.lon_min, params.lon_max]):
        bbox_matches_extent = False
        if dataset.spatial_extent is not None:
            extent_row = (
                await db.execute(
                    select(
                        func.ST_XMin(dataset.spatial_extent), func.ST_XMax(dataset.spatial_extent),
                        func.ST_YMin(dataset.spatial_extent), func.ST_YMax(dataset.spatial_extent),
                    )
                )
            ).one_or_none()
            if extent_row is not None and None not in extent_row:
                ext_lon_min, ext_lon_max, ext_lat_min, ext_lat_max = extent_row
                bbox_matches_extent = (
                    (params.lat_min is None or abs(params.lat_min - float(ext_lat_min)) < 0.001)
                    and (params.lat_max is None or abs(params.lat_max - float(ext_lat_max)) < 0.001)
                    and (params.lon_min is None or abs(params.lon_min - float(ext_lon_min)) < 0.001)
                    and (params.lon_max is None or abs(params.lon_max - float(ext_lon_max)) < 0.001)
                )

    if not (date_matches_extent and bbox_matches_extent):
        return None

    snapshot = await get_dataset_snapshot(dataset)
    if snapshot is None:
        return None
    return dataset, snapshot


async def get_timeseries(db: AsyncSession, params: TimeSeriesRequest) -> TimeSeriesResponse:
    if params.dataset_id:
        await validate_parameter_for_dataset(db, params.dataset_id, params.parameter)

    match = await _snapshot_if_default_shape(db, params)
    if match is not None:
        _dataset, snapshot = match
        if (
            snapshot.get("default_chart") is not None
            and snapshot["default_chart"]["parameter"] == params.parameter
        ):
            return TimeSeriesResponse.model_validate(snapshot["default_chart"]["response"])

    settings_row = await settings_service.get_settings(db)
    _check_date_range_limit(
        settings_row, params, max_days=settings_row.viz_max_date_range_days_timeseries
    )

    cache_key = _cache_key("timeseries", params)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return TimeSeriesResponse.model_validate(cached)

    has_temporal_data = (
        await dataset_has_temporal_dimension(db, params.dataset_id) if params.dataset_id else True
    )

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
            # A dataset with no time dimension has DatasetRecord.time NULL
            # on every row (Phase 2's nullability — "not every scientific
            # dataset has every dimension") — such a row can never belong
            # to a bucket and must be skipped, not passed into
            # season_bucket_date (which assumes a real date).
            if row.bucket is None:
                continue
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
            # date_trunc(unit, NULL) is NULL in SQL — a no-time-dimension
            # dataset's rows all land in one NULL-keyed group here rather
            # than being excluded by the GROUP BY itself, so this must be
            # skipped explicitly (same reasoning as the seasonal branch
            # above).
            if row.bucket is None:
                continue
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
        ClimatologyPoint(
            month=month_abbr[m],
            value=_mean(by_month[m]) if by_month.get(m) else None,
        )
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
        has_temporal_data=has_temporal_data,
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
        if grouped.get(StorageKind.CHUNKED_ARRAY.value):
            # Reuses the existing admin-configurable viz_max_grid_resolution
            # setting (already the ceiling on the interpolation grid's own
            # cell count) as the raw-point cap too, rather than a new
            # independent limit — see gridded_query_service.get_spatial_
            # points' docstring for the full rationale.
            settings_row = await settings_service.get_settings(db)
            max_grid_resolution = settings_row.viz_max_grid_resolution or 100
            max_points = max_grid_resolution**2
            for file in grouped[StorageKind.CHUNKED_ARRAY.value]:
                file_points = await bounded_to_thread(
                    gridded_query_service.get_spatial_points,
                    file, params.parameter, record_filter, max_points=max_points,
                )
                points.extend(
                    SpatialPointSchema(station=p.station, lat=p.lat, lon=p.lon, value=p.value) for p in file_points
                )

    return points


def _haversine_km(
    lat1: np.ndarray | float, lon1: np.ndarray | float, lat2: np.ndarray | float, lon2: np.ndarray | float
) -> np.ndarray:
    """Great-circle distance in km — either side may be a scalar or a
    numpy array (broadcasts normally). Used by _idw so IDW weighting
    reflects true geodesic closeness rather than raw lat/lon degree
    differences, which are NOT physically comparable to each other (at
    Bangladesh's ~20-23N latitude, 1 degree of longitude is only
    ~103-104 km while 1 degree of latitude is ~110-111 km — a real ~6%
    directional bias if degrees are treated as a flat Euclidean plane)."""
    lat1_r, lon1_r, lat2_r, lon2_r = np.radians(lat1), np.radians(lon1), np.radians(lat2), np.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1_r) * np.cos(lat2_r) * np.sin(dlon / 2.0) ** 2
    c = 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    return _EARTH_RADIUS_KM * c


def _idw(points: list[SpatialPointSchema], lats: np.ndarray, lons: np.ndarray, power: int = 2) -> np.ndarray:
    """Vectorized inverse-distance-weighting onto a regular grid — same
    weighting formula as bodp-frontend/src/lib/mock-data/stats.ts's
    idwGrid(), but distance is real geodesic distance (_haversine_km),
    not raw lat/lon degree differences — the weights (w = 1/d**power)
    are only ever used as a ratio (z = sum(w*v)/sum(w)), so the change
    from degrees to km has no effect on its own; only the *relative*
    accuracy of which points count as "closer" improves."""
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    z = np.zeros_like(lat_grid, dtype=float)
    w_sum = np.zeros_like(lat_grid, dtype=float)
    for p in points:
        d = _haversine_km(lat_grid, lon_grid, p.lat, p.lon)
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


async def get_visualization_job(db: AsyncSession, job_id: uuid.UUID) -> VisualizationJob:
    """Generic by-id fetch for any VisualizationJob row regardless of
    job_type — spatial_interpolation, comparison, and statistics jobs
    (Visualize Performance plan, Phase 4) all poll through this same
    helper, since the row shape and not-found handling are identical."""
    job = await db.get(VisualizationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Visualization job not found")
    return job


# --- Comparison (task 3) ---


async def get_profiles(db: AsyncSession, params: ProfilesRequest) -> ProfilesResponse:
    """Oceanographic Profiles module — either a single-variable depth
    profile (params.parameter) or a T-S diagram (params.temperature_
    parameter + params.salinity_parameter), dispatched across both
    storage tiers exactly like get_comparison/get_statistics: the legacy
    DatasetRecord SQL path always runs, PARQUET-backed files are queried
    via tabular_query_service and merged in. CHUNKED_ARRAY (Zarr) files
    are not queried here — see gridded_query_service's module docstring
    for why per-depth-level gridded extraction is out of scope for this
    pass (documented limitation, not a silent gap)."""
    is_ts_request = params.temperature_parameter is not None or params.salinity_parameter is not None
    if is_ts_request and params.parameter is not None:
        raise HTTPException(
            status_code=422,
            detail="Request either 'parameter' (depth profile) or both 'temperature_parameter' "
            "and 'salinity_parameter' (T-S diagram), not both request shapes at once.",
        )
    if is_ts_request and (params.temperature_parameter is None or params.salinity_parameter is None):
        raise HTTPException(
            status_code=422,
            detail="A T-S diagram request requires both 'temperature_parameter' and 'salinity_parameter'.",
        )
    if not is_ts_request and params.parameter is None:
        raise HTTPException(
            status_code=422,
            detail="Request requires either 'parameter' or both 'temperature_parameter'/'salinity_parameter'.",
        )

    if params.dataset_id:
        for parameter in filter(None, [params.parameter, params.temperature_parameter, params.salinity_parameter]):
            await validate_parameter_for_dataset(db, params.dataset_id, parameter)

    cache_key = _cache_key("profiles", params)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return ProfilesResponse.model_validate(cached)

    has_depth_data = (
        await dataset_has_depth_dimension(db, params.dataset_id) if params.dataset_id else True
    )
    depth_convention = (
        await dataset_depth_convention(db, params.dataset_id) if params.dataset_id else None
    )

    if not has_depth_data:
        response = ProfilesResponse(profiles=[], ts_pairs=[], has_depth_data=False, depth_convention=None)
        await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
        return response

    from app.services import catalog_service, tabular_query_service

    record_filter = _viz_filter_to_records_filter(params) if params.dataset_id else None
    non_legacy_grouped = await _non_legacy_dataset_files(db, params.dataset_id) if params.dataset_id else {}

    if is_ts_request:
        pairs: list[TSPairSchema] = []
        if params.dataset_id and record_filter is not None:
            legacy_rows = await catalog_service.get_ts_pairs_sql(
                db, params.dataset_id, params.temperature_parameter, params.salinity_parameter, record_filter
            )
            pairs.extend(
                TSPairSchema(depth_m=d, temperature=t, salinity=s, lat=lat, lon=lon, time=time, station=station)
                for d, t, s, lat, lon, time, station in legacy_rows
            )
            for file in non_legacy_grouped.get(StorageKind.PARQUET.value, []):
                rows = await bounded_to_thread(
                    tabular_query_service.get_ts_pairs,
                    file, params.temperature_parameter, params.salinity_parameter, record_filter,
                )
                pairs.extend(
                    TSPairSchema(
                        depth_m=r.depth_m, temperature=r.temperature, salinity=r.salinity,
                        lat=r.lat, lon=r.lon, time=r.time, station=r.station,
                    )
                    for r in rows
                )
        response = ProfilesResponse(
            profiles=[], ts_pairs=pairs, has_depth_data=True, depth_convention=depth_convention
        )
        await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
        return response

    # Single-variable depth profile.
    profiles_by_key: dict[tuple, StationProfileSchema] = {}

    def _merge_profile(station, lat, lon, time_val, depth_m, value) -> None:
        key = (station, time_val)
        existing = profiles_by_key.get(key)
        if existing is None:
            profiles_by_key[key] = StationProfileSchema(
                station=station, lat=lat, lon=lon, time=time_val,
                points=[ProfilePointSchema(depth_m=depth_m, value=value)],
            )
            return
        if existing.lat is None and lat is not None:
            existing.lat = lat
        if existing.lon is None and lon is not None:
            existing.lon = lon
        existing.points.append(ProfilePointSchema(depth_m=depth_m, value=value))

    if params.dataset_id and record_filter is not None:
        legacy_rows = await catalog_service.get_profile_sql(db, params.dataset_id, params.parameter, record_filter)
        for station, lat, lon, time_val, depth_m, value in legacy_rows:
            _merge_profile(station, lat, lon, time_val, depth_m, value)

        for file in non_legacy_grouped.get(StorageKind.PARQUET.value, []):
            station_profiles = await bounded_to_thread(
                tabular_query_service.get_profile, file, params.parameter, record_filter
            )
            for sp in station_profiles:
                for point in sp.points:
                    _merge_profile(sp.station, sp.lat, sp.lon, sp.time, point.depth_m, point.value)

    for profile in profiles_by_key.values():
        profile.points.sort(key=lambda p: p.depth_m)

    response = ProfilesResponse(
        profiles=list(profiles_by_key.values()),
        ts_pairs=[],
        has_depth_data=True,
        depth_convention=depth_convention,
    )
    await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
    return response


async def get_comparison(db: AsyncSession, params: ComparisonRequest) -> ComparisonJobResponse:
    """Sync-vs-Celery-job dispatch wrapper, same shape as create_spatial_
    request — a request whose non-legacy (Parquet/Zarr) files sum above
    VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES dispatches to Celery instead of
    computing in-process. See _compute_comparison for the actual work,
    which is unchanged and shared by both the sync and job paths (the
    Celery task calls it directly, so the two paths can never drift)."""
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
        return ComparisonJobResponse(status="complete", result=ComparisonResponse.model_validate(cached))

    non_legacy_grouped = (
        await _non_legacy_dataset_files(db, params.dataset_id) if params.dataset_id else {}
    )
    total_bytes = sum(
        file.file_size_bytes or 0 for files in non_legacy_grouped.values() for file in files
    )
    if total_bytes > settings.VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES:
        job = VisualizationJob(
            job_type="comparison", params=params.model_dump(mode="json"), status=VizJobStatus.QUEUED.value
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        from app.worker.tasks.visualize import run_comparison_job

        task = run_comparison_job.delay(str(job.id))
        job.celery_task_id = task.id
        await db.commit()

        return ComparisonJobResponse(status="queued", job_id=job.id)

    result = await _compute_comparison(db, params, non_legacy_grouped)
    return ComparisonJobResponse(status="complete", result=result)


async def _compute_comparison(
    db: AsyncSession, params: ComparisonRequest, non_legacy_grouped: dict[str, list[DatasetFile]]
) -> ComparisonResponse:
    # Recomputed rather than threaded through from get_comparison — cheap
    # deterministic hash, and keeps this function callable standalone
    # (the Celery job path calls it directly, with no wrapper-scope
    # cache_key available).
    cache_key = _cache_key("comparison", params)

    has_temporal_data = (
        await dataset_has_temporal_dimension(db, params.dataset_id) if params.dataset_id else True
    )

    record_filter = _viz_filter_to_records_filter(params) if params.dataset_id else None

    # Visualize Module audit fix (Multivariable — Non-Temporal Datasets):
    # date is the natural pairing key for a dataset WITH a time
    # dimension, but it is not the only scientifically valid one — Pearson
    # correlation/regression only require that x[i] and y[i] come from the
    # same underlying observation. For a dataset confirmed to have no
    # temporal dimension at all, (lat, lon) is used instead: two
    # parameters' values at the same location are exactly as validly
    # paired as two values on the same date would be for a temporal
    # dataset. This branch is only ever taken when has_temporal_data is
    # False — a temporal dataset always uses the original exact-date path
    # below, unchanged.
    if params.dataset_id and not has_temporal_data:
        # The legacy-table lookup below runs on the shared AsyncSession,
        # which is NOT safe to call concurrently from multiple coroutines
        # (SQLAlchemy's AsyncSession serializes on one connection) — so
        # this part stays a sequential loop, one query per parameter, same
        # as before. It is a cheap indexed lookup either way; the
        # expensive part is the Parquet/Zarr I/O below, which runs in a
        # thread pool and has no such restriction.
        legacy_by_location: dict[str, dict[tuple[float, float], list[float]]] = {}
        for parameter in params.parameters:
            by_latlon: dict[tuple[float, float], list[float]] = defaultdict(list)
            legacy_query = select(DatasetRecord.lat, DatasetRecord.lon, DatasetRecord.value).where(
                DatasetRecord.parameter == parameter
            )
            legacy_query = _apply_viz_filters(legacy_query, params)
            legacy_result = await db.execute(legacy_query)
            for row in legacy_result.all():
                if row.lat is not None and row.lon is not None:
                    by_latlon[(_round_coord(float(row.lat)), _round_coord(float(row.lon)))].append(
                        float(row.value)
                    )
            legacy_by_location[parameter] = by_latlon

        async def _fetch_by_location(parameter: str) -> dict[tuple[float, float], list[float]]:
            by_latlon = legacy_by_location[parameter]

            if non_legacy_grouped:
                from app.services import gridded_query_service, tabular_query_service

                for file in non_legacy_grouped.get(StorageKind.PARQUET.value, []):
                    raw = await bounded_to_thread(
                        tabular_query_service.get_raw_values_with_location, file, parameter, record_filter
                    )
                    for lat, lon, value in raw:
                        if lat is not None and lon is not None:
                            by_latlon[(_round_coord(lat), _round_coord(lon))].append(value)
                for file in non_legacy_grouped.get(StorageKind.CHUNKED_ARRAY.value, []):
                    # get_spatial_points, not get_raw_values — the latter
                    # spatially averages an entire grid into one value per
                    # timestep (no per-cell identity survives), which
                    # cannot support per-location pairing at all. This
                    # reuses the SAME existing per-cell query Spatial
                    # Mapping's point layer already relies on (Zarr query
                    # logic itself is unmodified — only called from a new
                    # place), correctly returning one (lat, lon, value)
                    # per grid cell regardless of whether the variable has
                    # a time dimension.
                    points = await bounded_to_thread(
                        gridded_query_service.get_spatial_points, file, parameter, record_filter
                    )
                    for point in points:
                        by_latlon[(_round_coord(point.lat), _round_coord(point.lon))].append(point.value)

            return by_latlon

        # Phase 2 (Visualize Performance plan): each parameter's Parquet/
        # Zarr fetch is independent (different `parameter` filter,
        # disjoint results, thread-pool I/O only — no shared AsyncSession
        # access past this point) — sequentially awaiting them paid N× the
        # wall-clock for N parameters. gather runs them concurrently;
        # bounded_to_thread's semaphore (QUERY_CONCURRENCY_LIMIT_PER_WORKER)
        # still caps actual CPU-bound concurrency, so this cannot
        # oversubscribe the worker.
        fetched = await asyncio.gather(*(_fetch_by_location(p) for p in params.parameters))
        series_by_location: dict[str, dict[tuple[float, float], list[float]]] = dict(
            zip(params.parameters, fetched)
        )

        x_param, y_param = params.parameters[0], params.parameters[1]
        x_by_loc = {loc: _mean(vals) for loc, vals in series_by_location[x_param].items()}
        y_by_loc = {loc: _mean(vals) for loc, vals in series_by_location[y_param].items()}
        common_locs = sorted(set(x_by_loc) & set(y_by_loc))
        x_vals = [x_by_loc[loc] for loc in common_locs]
        y_vals = [y_by_loc[loc] for loc in common_locs]

        r = _pearson(x_vals, y_vals)
        slope, intercept = _linreg_xy(x_vals, y_vals)

        scatter = ScatterSchema(
            x_parameter=x_param,
            y_parameter=y_param,
            x=x_vals,
            y=y_vals,
            r=r,
            regression=RegressionSchema(slope=slope, intercept=intercept, r_squared=r**2),
            n=len(common_locs),
            pairing_method="lat_lon_match",
        )

        n = len(params.parameters)
        matrix = [[0.0] * n for _ in range(n)]
        n_matrix = [[0] * n for _ in range(n)]
        for i in range(n):
            n_matrix[i][i] = len(series_by_location[params.parameters[i]])
            for j in range(n):
                if i == j:
                    matrix[i][j] = 1.0
                    continue
                pi, pj = params.parameters[i], params.parameters[j]
                by_i = {loc: _mean(vals) for loc, vals in series_by_location[pi].items()}
                by_j = {loc: _mean(vals) for loc, vals in series_by_location[pj].items()}
                common = sorted(set(by_i) & set(by_j))
                matrix[i][j] = _pearson([by_i[loc] for loc in common], [by_j[loc] for loc in common])
                n_matrix[i][j] = len(common)

        # series_by_parameter stays empty for a non-temporal dataset — its
        # only consumer (ComparisonModule.tsx's "Trend Over Time" chart)
        # is itself gated on has_temporal_data client-side and never reads
        # it in this case, matching how Statistics already leaves its own
        # date-bucketed sections empty (not fabricated) for the same
        # dataset shape.
        response = ComparisonResponse(
            scatter=scatter,
            series_by_parameter={},
            correlation_matrix=CorrelationMatrixSchema(
                parameters=params.parameters, matrix=matrix, n=n_matrix, pairing_method="lat_lon_match"
            ),
            has_temporal_data=has_temporal_data,
        )
        await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
        return response

    # Same AsyncSession-concurrency constraint as the non-temporal branch
    # above: the DatasetRecord query must stay a sequential loop (one
    # shared `db` cannot serve concurrent .execute() calls), but it's a
    # cheap indexed lookup — the expensive Parquet/Zarr I/O below is what
    # actually benefits from concurrent dispatch.
    legacy_by_date: dict[str, dict[date, list[float]]] = {}
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
            # A no-time-dimension dataset's rows all have time=NULL, which
            # GROUP BY DatasetRecord.time collapses into one NULL-keyed
            # row rather than excluding — must be skipped explicitly, same
            # as the non_legacy_grouped paths below already do.
            if row.time is not None:
                by_date[row.time].append(float(row.avg_value))
        legacy_by_date[parameter] = by_date

    async def _fetch_series(parameter: str) -> list[SeriesPointSchema]:
        by_date = legacy_by_date[parameter]

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

        return [SeriesPointSchema(date=d, value=_mean(vals)) for d, vals in sorted(by_date.items())]

    # Phase 2 (Visualize Performance plan): same concurrent-gather fix as
    # the non-temporal branch above — each parameter's Parquet/Zarr fetch
    # is independent and thread-pool-only past this point, so N
    # parameters no longer pay N× the wall-clock.
    fetched_series = await asyncio.gather(*(_fetch_series(p) for p in params.parameters))
    series_by_parameter: dict[str, list[SeriesPointSchema]] = dict(zip(params.parameters, fetched_series))

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
        regression=RegressionSchema(slope=slope, intercept=intercept, r_squared=r**2),
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
    n_matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        n_matrix[i][i] = len(series_by_parameter[params.parameters[i]])
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
                continue
            pi, pj = params.parameters[i], params.parameters[j]
            by_i = {p.date: p.value for p in series_by_parameter[pi]}
            by_j = {p.date: p.value for p in series_by_parameter[pj]}
            common = sorted(set(by_i) & set(by_j))
            matrix[i][j] = _pearson([by_i[d] for d in common], [by_j[d] for d in common])
            n_matrix[i][j] = len(common)

    response = ComparisonResponse(
        scatter=scatter,
        series_by_parameter=series_by_parameter,
        correlation_matrix=CorrelationMatrixSchema(
            parameters=params.parameters, matrix=matrix, n=n_matrix, pairing_method="exact_date_match"
        ),
        has_temporal_data=has_temporal_data,
    )
    await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
    return response


def _round_coord(value: float, precision: int = 6) -> float:
    """Rounds a lat/lon value for use as a pairing-join key (Visualize
    Module audit fix, Multivariable — Non-Temporal Datasets) — matches
    DatasetRecord.lat/.lon's own column precision (NUMERIC(9,6), see
    models/catalog.py) so a legacy-path value and a Parquet-path value
    for the genuinely same source location always round to the identical
    key, never missing a match over float representation noise."""
    return round(value, precision)


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


async def get_statistics(db: AsyncSession, params: StatisticsRequest) -> StatisticsJobResponse:
    """Sync-vs-Celery-job dispatch wrapper — see get_comparison's
    docstring for the full reasoning, identical shape here. The snapshot
    shortcut and Redis cache checks stay in this wrapper (they're the
    fast paths precisely so the heavy Celery path is never reached for a
    request they already cover); _compute_statistics is the shared,
    unchanged actual computation."""
    if params.dataset_id:
        await validate_parameter_for_dataset(db, params.dataset_id, params.parameter)

    # Visualize Performance plan, Phase 1: same extent-match snapshot
    # shortcut get_timeseries uses, for the snapshot's precomputed
    # default_statistics section — see _snapshot_if_default_shape's
    # docstring for the full reasoning.
    match = await _snapshot_if_default_shape(db, params)
    if match is not None:
        _dataset, snapshot = match
        default_statistics = snapshot.get("default_statistics")
        if default_statistics is not None and default_statistics["parameter"] == params.parameter:
            return StatisticsJobResponse(
                status="complete",
                result=StatisticsResponse.model_validate(default_statistics["response"]),
            )

    settings_row = await settings_service.get_settings(db)
    _check_date_range_limit(
        settings_row, params, max_days=settings_row.viz_max_date_range_days_statistics
    )

    cache_key = _cache_key("statistics", params)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return StatisticsJobResponse(status="complete", result=StatisticsResponse.model_validate(cached))

    non_legacy_grouped = (
        await _non_legacy_dataset_files(db, params.dataset_id) if params.dataset_id else {}
    )
    total_bytes = sum(
        file.file_size_bytes or 0 for files in non_legacy_grouped.values() for file in files
    )
    if total_bytes > settings.VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES:
        job = VisualizationJob(
            job_type="statistics", params=params.model_dump(mode="json"), status=VizJobStatus.QUEUED.value
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        from app.worker.tasks.visualize import run_statistics_job

        task = run_statistics_job.delay(str(job.id))
        job.celery_task_id = task.id
        await db.commit()

        return StatisticsJobResponse(status="queued", job_id=job.id)

    result = await _compute_statistics(db, params, non_legacy_grouped)
    return StatisticsJobResponse(status="complete", result=result)


async def _compute_statistics(
    db: AsyncSession, params: StatisticsRequest, non_legacy_grouped: dict[str, list[DatasetFile]]
) -> StatisticsResponse:
    # Recomputed rather than threaded through — see _compute_comparison's
    # identical comment for why.
    cache_key = _cache_key("statistics", params)

    has_temporal_data = (
        await dataset_has_temporal_dimension(db, params.dataset_id) if params.dataset_id else True
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

    # Gridded (CHUNKED_ARRAY) files' raw values, captured WITH their date
    # here — folded into `weighted` below instead of a second independent
    # _merged_timeseries call, so this file/parameter's Zarr data is read
    # and reduced exactly once for this whole endpoint (Performance &
    # Behavior investigation, Phase 6: confirmed via direct measurement
    # that querying the same file twice — once here for box_plot, once
    # more inside _merged_timeseries for the series below — roughly
    # doubled this endpoint's cost for no benefit, since get_raw_values'
    # per-timestep values already ARE the daily series once grouped by
    # date; equivalent to get_timeseries_aggregate(resolution="daily")
    # for any native-daily-or-finer time axis, which is what this
    # endpoint has always requested).
    gridded_daily: dict[date, list[float]] = defaultdict(list)

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
            for station, t, value in raw:
                by_station[station or f"file:{file.id}"].append(value)
                if t is not None:
                    gridded_daily[t].append(value)

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
        # A no-time-dimension dataset's rows all have time=NULL, which
        # GROUP BY DatasetRecord.time collapses into one NULL-keyed row —
        # must be skipped explicitly (same reasoning as get_timeseries/
        # get_comparison's identical guards).
        if row.time is None:
            continue
        weighted[row.time][0] += float(row.avg_value) * row.n
        weighted[row.time][1] += row.n

    if params.dataset_id:
        # include_gridded=False: the CHUNKED_ARRAY contribution is folded
        # in separately below, from gridded_daily (already fetched above
        # for box_plot) — not re-queried here.
        non_legacy_rows = await _merged_timeseries(
            db, params.dataset_id, params.parameter, params, resolution="daily", include_gridded=False
        )
        for bucket_date, avg, n in non_legacy_rows:
            weighted[bucket_date][0] += avg * n
            weighted[bucket_date][1] += n

        # Same weighting convention _merged_timeseries' own gridded branch
        # uses: no true per-bucket row count for a spatial mean, so each
        # BUCKET (not each raw reading within it) is treated as one
        # unweighted observation — average any sub-daily raw readings
        # down to one daily value first (matching what get_timeseries_
        # aggregate's own .resample(time="1D").mean() would have
        # produced), THEN fold that one value in at weight=1. Folding in
        # every raw reading at weight=1 instead would silently let a
        # gridded file with finer-than-daily native resolution outweigh
        # legacy/Parquet contributions to the same bucket in proportion
        # to its sampling rate, not its true bucket-level significance.
        for bucket_date, values in gridded_daily.items():
            weighted[bucket_date][0] += sum(values) / len(values)
            weighted[bucket_date][1] += 1

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
        has_temporal_data=has_temporal_data,
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


# --- Coverage (Visualization & Filter Reliability investigation, issue #5) ---


async def get_visualize_coverage(db: AsyncSession, params: CoverageRequest) -> CoverageResponse:
    """Reuses catalog_service.get_matching_record_counts (already
    Parquet/Zarr-aware) rather than a new counting mechanism, matching
    the exact "Matching Records / Dataset Records / Coverage" summary
    the catalog detail page's own useDatasetFilters.ts already shows.

    NOT cheap for a large gridded (Zarr) dataset — confirmed via direct
    measurement (Performance & Behavior investigation): the underlying
    matching count is an exact, real xarray .count() over the filtered
    cell range, which requires reading actual chunk data from MinIO, not
    metadata (~6s for a 15M-cell matching range on a real dataset). Kept
    exact rather than switched to an estimate (explicit decision — see
    docs/PERFORMANCE_FIX_PLAN.md Phase 4). The same Redis caching
    already used by get_timeseries/get_comparison/get_statistics below
    is what makes repeated/interactive use of this endpoint tolerable —
    this endpoint had none until now, unlike its three siblings."""
    if params.dataset_id is None:
        return CoverageResponse(matching_count=0, dataset_total_count=0, coverage_percent=0.0, unit="records")

    cache_key = _cache_key("coverage", params)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return CoverageResponse.model_validate(cached)

    from app.services import catalog_service

    record_filter = _viz_filter_to_records_filter(params)
    if params.parameter:
        record_filter.parameters = [params.parameter]

    matching, total = await catalog_service.get_matching_record_counts(db, params.dataset_id, record_filter)
    coverage_percent = (matching / total * 100.0) if total > 0 else 0.0

    grouped = await _non_legacy_dataset_files(db, params.dataset_id)
    has_gridded = bool(grouped.get(StorageKind.CHUNKED_ARRAY.value))
    unit: Literal["records", "cells"] = "cells" if has_gridded else "records"

    notes: list[str] = []
    if params.station and has_gridded:
        notes.append(
            "Station filter does not restrict this dataset's gridded (Zarr) data — "
            "a station is not a meaningful concept for a spatial grid, so gridded "
            "contributions are included regardless of the selected station."
        )

    response = CoverageResponse(
        matching_count=matching,
        dataset_total_count=total,
        coverage_percent=coverage_percent,
        unit=unit,
        notes=notes,
    )
    await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_CACHE_TTL_SECONDS)
    return response
