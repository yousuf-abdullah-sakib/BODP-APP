import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.requests import SpatialBoundsSchema


class VizFilterParams(BaseModel):
    """Shared filter shape across all 4 /visualize/* endpoints — mirrors
    the frontend's useVizFilters.ts VizFilters interface exactly (category/
    parameter/station/date range/resolution/bbox/depth), cutting across
    datasets rather than being scoped to one, unlike catalog's
    RecordsFilter."""

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
    value: float


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
    method: Literal["idw", "kriging", "nearest"] = "idw"
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


class ScatterSchema(BaseModel):
    x_parameter: str
    y_parameter: str
    x: list[float]
    y: list[float]
    r: float
    regression: RegressionSchema


class CorrelationMatrixSchema(BaseModel):
    parameters: list[str]
    matrix: list[list[float]]


class ComparisonRequest(VizFilterParams):
    parameters: list[str] = Field(min_length=2)


class ComparisonResponse(BaseModel):
    scatter: ScatterSchema
    series_by_parameter: dict[str, list[SeriesPointSchema]]
    correlation_matrix: CorrelationMatrixSchema


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
