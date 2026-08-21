import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.catalog import SpatialBBox
from app.schemas.requests import SpatialBoundsSchema


class VizFilterParams(BaseModel):
    """Shared filter shape across all 4 /visualize/* endpoints — mirrors
    the frontend's useVizFilters.ts VizFilters interface exactly (category/
    parameter/station/date range/resolution/bbox/depth).

    dataset_id (PLAN.md Phase 4) scopes every query to one dataset's
    approved schema, once the frontend's dataset selector is in use.
    Nullable/optional for back-compat — omitting it preserves the
    pre-Phase-4 cross-dataset query behavior exactly (the fallback path)."""

    dataset_id: uuid.UUID | None = None
    category: str | None = None
    station: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    resolution: Literal["daily", "monthly", "seasonal", "annual"] = "monthly"
    lat_min: float | None = None
    lat_max: float | None = None
    lon_min: float | None = None
    lon_max: float | None = None
    depth_min: float | None = None
    depth_max: float | None = None


# --- Coverage (Visualization & Filter Reliability investigation, issue #5) ---


class CoverageRequest(VizFilterParams):
    """dataset_id is required in practice (coverage against "every
    dataset" isn't a meaningful concept the way a single-dataset filter
    summary is) — kept optional at the type level only to inherit
    VizFilterParams cleanly; the service function returns an all-zero
    response rather than erroring when it's omitted, matching how the
    rest of this module treats a missing dataset_id as "the fallback,
    unscoped mode" rather than a request error."""

    parameter: str | None = None


class CoverageResponse(BaseModel):
    matching_count: int
    dataset_total_count: int
    coverage_percent: float
    # "records" for a legacy/Parquet-only dataset, "cells" once any of
    # the dataset's files is gridded (CHUNKED_ARRAY) — matches what
    # catalog_service/gridded_query_service.get_matching_record_counts
    # actually counts (grid elements, not rows) for that storage kind,
    # so the label never claims something scientifically inaccurate.
    unit: Literal["records", "cells"]
    # Plain-language notes about filters that were accepted but could not
    # be meaningfully applied to part of this dataset's data (e.g. a
    # station filter against a gridded file) — surfaced explicitly rather
    # than silently producing a coverage number that looks more precise
    # than it is. Empty when nothing applies.
    notes: list[str] = []


# --- Time series (task 1) ---


class SeriesPointSchema(BaseModel):
    date: date
    value: float


class TimeSeriesStats(BaseModel):
    mean: float
    median: float
    std: float
    min: float
    max: float
    trend_per_year: float
    count: int


class SeasonalPoint(BaseModel):
    label: str
    value: float


class ClimatologyPoint(BaseModel):
    month: str
    # None (not 0.0) when this month has zero observations in the
    # filtered series — a genuinely-empty month must be distinguishable
    # from a real 0.0 reading.
    value: float | None


class RateOfChangePoint(BaseModel):
    date: date
    delta: float


class AnomalyPoint(BaseModel):
    date: date
    anomaly: float


class TimeSeriesRequest(VizFilterParams):
    parameter: str = Field(min_length=1)


class TimeSeriesResponse(BaseModel):
    series: list[SeriesPointSchema]
    stats: TimeSeriesStats
    moving_average: list[float]
    trend_line: list[float]
    seasonal: list[SeasonalPoint]
    climatology: list[ClimatologyPoint]
    rate_of_change: list[RateOfChangePoint]
    anomaly: list[AnomalyPoint]
    # False only when dataset_id is set and that dataset has no temporal
    # dimension at all (no DatasetVariable with data_type="temporal") —
    # distinguishes "this dataset genuinely has no time axis" from "the
    # current filters just happen to match zero points", so the frontend
    # can show a clear explanation instead of an ambiguous empty chart.
    # Always True when dataset_id is omitted (the original cross-dataset
    # query mode, which never had this concept).
    has_temporal_data: bool = True


# --- Spatial (task 2) ---


class SpatialPointSchema(BaseModel):
    station: str
    lat: float
    lon: float
    value: float


class SpatialGrid(BaseModel):
    lats: list[float]
    lons: list[float]
    z: list[list[float]]


class SpatialRequest(VizFilterParams):
    parameter: str = Field(min_length=1)
    method: Literal["idw", "nearest"] = "idw"
    # Grid resolution (cell count per axis) — deliberately NOT named
    # `resolution` to avoid colliding with VizFilterParams.resolution
    # (temporal aggregation granularity), an unrelated concept.
    grid_resolution: int = Field(default=40, ge=5, le=100)
    bounds: SpatialBoundsSchema


class SpatialResponse(BaseModel):
    status: Literal["complete", "queued"]
    job_id: uuid.UUID | None = None
    points: list[SpatialPointSchema] | None = None
    grid: SpatialGrid | None = None
    method_used: str | None = None


class SpatialJobStatusResponse(BaseModel):
    id: uuid.UUID
    status: str
    points: list[SpatialPointSchema] | None = None
    grid: SpatialGrid | None = None
    method_used: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


# --- Comparison (task 3) ---


class RegressionSchema(BaseModel):
    slope: float
    intercept: float
    # Coefficient of determination (r**2) — trivial to compute since r
    # is already available at the call site, but not worth making the
    # caller re-derive it themselves.
    r_squared: float


class ScatterSchema(BaseModel):
    x_parameter: str
    y_parameter: str
    x: list[float]
    y: list[float]
    r: float
    regression: RegressionSchema
    # Transparency fields (Visualize Phase A) — the paired sample size
    # backing r/regression/the scatter plot, and how the two parameters'
    # observations were paired. Additive only: does not change x/y/r/
    # regression, which are computed exactly as before. n == len(x) ==
    # len(y) always; exposed explicitly so the frontend/user doesn't have
    # to infer sample size by counting array length themselves.
    n: int
    pairing_method: str


class CorrelationMatrixSchema(BaseModel):
    parameters: list[str]
    matrix: list[list[float]]
    # Paired sample size backing each cell's r — same shape as matrix.
    # Each cell is its own independent pairwise date-intersection
    # (pairwise-complete, not listwise-complete), so this genuinely
    # varies per cell, not one matrix-wide count. Diagonal is each
    # parameter's own series length (not a "paired" count, since a
    # parameter isn't paired with itself, but the closest meaningful
    # analog: how many points that series has).
    n: list[list[int]]
    # Visualize Module audit fix (Multivariable — Non-Temporal Datasets):
    # same meaning/values as ScatterSchema.pairing_method, one level up
    # since the whole matrix always uses the same method for one request
    # (a dataset either has a temporal dimension or it doesn't) — never
    # mixed cell-to-cell.
    pairing_method: str


class ComparisonRequest(VizFilterParams):
    parameters: list[str] = Field(min_length=2)


class ComparisonResponse(BaseModel):
    scatter: ScatterSchema
    series_by_parameter: dict[str, list[SeriesPointSchema]]
    correlation_matrix: CorrelationMatrixSchema
    # See TimeSeriesResponse.has_temporal_data — same meaning here.
    has_temporal_data: bool = True


# Visualize Performance plan, Phase 4: same status/job_id envelope
# SpatialResponse already uses for genuinely heavy requests — `result` is
# the same ComparisonResponse shape, just nested rather than inlined, so
# a "queued" response and a polled-complete VisualizationJob.result can
# both be validated against one shared schema (see ComparisonJobStatus
# below).
class ComparisonJobResponse(BaseModel):
    status: Literal["complete", "queued"]
    job_id: uuid.UUID | None = None
    result: ComparisonResponse | None = None


class ComparisonJobStatus(BaseModel):
    id: uuid.UUID
    status: str
    result: ComparisonResponse | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


# --- Statistics (task 4) ---


class BoxPlotSeries(BaseModel):
    station: str
    values: list[float]


class AnnualAnomaly(BaseModel):
    year: int
    anomaly: float


class DecompositionSchema(BaseModel):
    dates: list[date]
    trend: list[float]
    seasonal: list[float]
    residual: list[float]


class CalendarHeatmap(BaseModel):
    years: list[int]
    months: list[str]
    z: list[list[float]]


class StatisticsRequest(VizFilterParams):
    parameter: str = Field(min_length=1)


class StatisticsResponse(BaseModel):
    box_plot: list[BoxPlotSeries]
    histogram: list[float]
    annual_anomalies: list[AnnualAnomaly]
    decomposition: DecompositionSchema
    calendar_heatmap: CalendarHeatmap
    # See TimeSeriesResponse.has_temporal_data — same meaning here.
    has_temporal_data: bool = True


# Visualize Performance plan, Phase 4 — see ComparisonJobResponse/
# ComparisonJobStatus above for the full reasoning, identical shape here.
class StatisticsJobResponse(BaseModel):
    status: Literal["complete", "queued"]
    job_id: uuid.UUID | None = None
    result: StatisticsResponse | None = None


class StatisticsJobStatus(BaseModel):
    id: uuid.UUID
    status: str
    result: StatisticsResponse | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


# --- Oceanographic Profiles ---
#
# depth_convention is surfaced (never silently corrected/flipped) so the
# frontend can render the depth axis honestly — "assumed_positive_down"/
# "assumed_negative_up" reflect what dataset ingestion inferred from the
# detected depth column's own sign (see csv_parser.py/mat_parser.py/
# netcdf_parser.py's depth_convention derivation), never a guess made here.


class ProfilePointSchema(BaseModel):
    depth_m: float
    value: float


class StationProfileSchema(BaseModel):
    station: str | None
    lat: float | None
    lon: float | None
    time: date | None
    points: list[ProfilePointSchema]


class TSPairSchema(BaseModel):
    depth_m: float | None
    temperature: float
    salinity: float
    lat: float | None
    lon: float | None
    time: date | None
    station: str | None


class ProfilesRequest(VizFilterParams):
    """Either `parameter` (single-variable depth profile) or both
    `temperature_parameter`+`salinity_parameter` (T-S diagram) must be
    supplied — never both request shapes at once, enforced in
    visualize_service.get_profiles rather than at the schema level so the
    error message can name exactly what's missing."""

    parameter: str | None = None
    temperature_parameter: str | None = None
    salinity_parameter: str | None = None


class ProfilesResponse(BaseModel):
    profiles: list[StationProfileSchema] = Field(default_factory=list)
    ts_pairs: list[TSPairSchema] = Field(default_factory=list)
    # False means this dataset has no detected depth/vertical coordinate
    # at all — distinguishes "no depth dimension" from "a depth dimension
    # exists but current filters match nothing," same has_temporal_data
    # pattern the other 3 analysis endpoints already use.
    has_depth_data: bool = True
    depth_convention: Literal["assumed_positive_down", "assumed_negative_up"] | None = None


# --- Dataset scoping (PLAN.md Phase 4) ---


class VisualizableDatasetSummary(BaseModel):
    """One entry in the Visualize module's dataset selector — only
    datasets an admin has reviewed (Phase 3) AND that have at least one
    approved Visualization Variable ever appear here."""

    id: uuid.UUID
    code: str
    title: str
    variables: list[str]
    # From catalog_service.get_dataset_temporal_extent — (None, None) for
    # a dataset with no temporal dimension at all (never fabricated).
    temporal_start: date | None
    temporal_end: date | None
    # From Dataset.spatial_extent (reliably set by real ingestion, unlike
    # temporal_start/end — confirmed by reading worker/tasks/ingestion.py's
    # union-merge logic and real dev data directly) — None for a dataset
    # with no recorded spatial extent at all (never fabricated). Lets
    # Spatial Mapping default its interpolation view to the SELECTED
    # dataset's real coverage instead of a hardcoded region (Performance &
    # Behavior investigation, Phase 7).
    spatial_extent: SpatialBBox | None = None


# --- Stations (supporting useVizFilters' filteredStations) ---


class VizStationOption(BaseModel):
    code: str
    name: str
    lat: float
    lon: float
    depth_m: float | None = None

    model_config = {"from_attributes": True}


# --- Boundary shapefiles (task 5) ---


class BoundaryShapefileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    geojson: dict
    is_default: bool = False


class BoundaryShapefileSummary(BaseModel):
    id: uuid.UUID
    name: str
    geojson: dict
    uploaded_at: datetime
    is_default: bool

    model_config = {"from_attributes": True}


# --- Visualization export settings (admin) ---


class VizExportSettingsSchema(BaseModel):
    viz_export_temporal_enabled: bool = True
    viz_export_spatial_enabled: bool = True
    viz_export_comparison_enabled: bool = True
    viz_export_statistics_enabled: bool = True

    model_config = {"from_attributes": True}


class VizExportSettingsUpdate(BaseModel):
    viz_export_temporal_enabled: bool | None = None
    viz_export_spatial_enabled: bool | None = None
    viz_export_comparison_enabled: bool | None = None
    viz_export_statistics_enabled: bool | None = None


# --- Visualization compute limits (admin) ---
#
# Guards against oversized /visualize/* requests (huge interpolation grid,
# huge AOI, huge date range) that could otherwise exhaust worker memory/
# CPU. Every limit is independently nullable, and null uniformly means
# "unlimited" for that specific limit — an admin can raise, lower, or
# fully disable each of the 6 limits from the database with no code
# change (Master Plan-adjacent follow-up: "make all visualization limits
# fully configurable, including Unlimited, for every limit").


class VizComputeLimitsSchema(BaseModel):
    viz_max_grid_resolution: int | None = 100
    viz_max_aoi_km2: float | None = 500.0
    viz_max_date_range_days_spatial: int | None = 3650
    viz_max_date_range_days_timeseries: int | None = None
    viz_max_date_range_days_comparison: int | None = None
    viz_max_date_range_days_statistics: int | None = None

    model_config = {"from_attributes": True}


class VizComputeLimitsUpdate(BaseModel):
    viz_max_grid_resolution: int | None = Field(default=None, ge=5, le=500)
    viz_max_aoi_km2: float | None = Field(default=None, gt=0)
    viz_max_date_range_days_spatial: int | None = Field(default=None, gt=0)
    viz_max_date_range_days_timeseries: int | None = Field(default=None, gt=0)
    viz_max_date_range_days_comparison: int | None = Field(default=None, gt=0)
    viz_max_date_range_days_statistics: int | None = Field(default=None, gt=0)
