// Mirrors backend/app/schemas/visualize.py.

export interface VizFilterParams {
  dataset_id?: string | null;
  category?: string | null;
  station?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  resolution?: "daily" | "monthly" | "seasonal" | "annual";
  lat_min?: number | null;
  lat_max?: number | null;
  lon_min?: number | null;
  lon_max?: number | null;
  depth_min?: number | null;
  depth_max?: number | null;
}

export interface CoverageRequest extends VizFilterParams {
  parameter?: string | null;
}

export interface CoverageResponse {
  matching_count: number;
  dataset_total_count: number;
  coverage_percent: number;
  /** "cells" once the dataset has any gridded (Zarr) file, "records" otherwise. */
  unit: "records" | "cells";
  /** Plain-language notes about filters that could not be meaningfully applied to part of this dataset. */
  notes: string[];
}

export interface SeriesPoint {
  date: string;
  value: number;
}

export interface TimeSeriesStats {
  mean: number;
  median: number;
  std: number;
  min: number;
  max: number;
  trend_per_year: number;
  count: number;
}

export interface SeasonalPoint {
  label: string;
  value: number;
}

export interface ClimatologyPoint {
  month: string;
  /** null when this month has zero observations in the filtered series. */
  value: number | null;
}

export interface RateOfChangePoint {
  date: string;
  delta: number;
}

export interface AnomalyPoint {
  date: string;
  anomaly: number;
}

export interface TimeSeriesRequest extends VizFilterParams {
  parameter: string;
}

export interface TimeSeriesResponse {
  series: SeriesPoint[];
  stats: TimeSeriesStats;
  moving_average: number[];
  trend_line: number[];
  seasonal: SeasonalPoint[];
  climatology: ClimatologyPoint[];
  rate_of_change: RateOfChangePoint[];
  anomaly: AnomalyPoint[];
  /** False only when the selected dataset genuinely has no time dimension at all. */
  has_temporal_data: boolean;
}

export interface SpatialPoint {
  station: string;
  lat: number;
  lon: number;
  value: number;
}

export interface SpatialGrid {
  lats: number[];
  lons: number[];
  z: number[][];
}

export type InterpolationMethod = "idw" | "nearest";

export interface SpatialBounds {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}

export interface SpatialRequest extends VizFilterParams {
  parameter: string;
  method: InterpolationMethod;
  grid_resolution: number;
  bounds: SpatialBounds;
}

export interface SpatialResponse {
  status: "complete" | "queued";
  job_id?: string | null;
  points?: SpatialPoint[] | null;
  grid?: SpatialGrid | null;
  method_used?: string | null;
}

export interface SpatialJobStatusResponse {
  id: string;
  status: string;
  points?: SpatialPoint[] | null;
  grid?: SpatialGrid | null;
  method_used?: string | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface RegressionResult {
  slope: number;
  intercept: number;
  r_squared: number;
}

export interface ScatterResult {
  x_parameter: string;
  y_parameter: string;
  x: number[];
  y: number[];
  r: number;
  regression: RegressionResult;
  /** Paired sample size backing r/regression — x.length === y.length === n. */
  n: number;
  /** How the two parameters' observations were paired (currently always "exact_date_match"). */
  pairing_method: string;
}

export interface CorrelationMatrix {
  parameters: string[];
  matrix: number[][];
  /** Paired sample size backing each cell's r — same shape as matrix. */
  n: number[][];
  /** How every cell's observations were paired — "exact_date_match" or "lat_lon_match" (no time dimension). */
  pairing_method: string;
}

export interface ComparisonRequest extends VizFilterParams {
  parameters: string[];
}

export interface ComparisonResponse {
  scatter: ScatterResult;
  series_by_parameter: Record<string, SeriesPoint[]>;
  correlation_matrix: CorrelationMatrix;
  /** False only when the selected dataset genuinely has no time dimension at all. */
  has_temporal_data: boolean;
}

/** Sync-vs-Celery-job envelope (Visualize Performance plan, Phase 4) —
 * same status/job_id pattern as SpatialResponse. `result` is populated
 * only when status is "complete". */
export interface ComparisonJobResponse {
  status: "complete" | "queued";
  job_id?: string | null;
  result?: ComparisonResponse | null;
}

export interface ComparisonJobStatus {
  id: string;
  status: string;
  result?: ComparisonResponse | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface BoxPlotSeries {
  station: string;
  values: number[];
}

export interface AnnualAnomaly {
  year: number;
  anomaly: number;
}

export interface Decomposition {
  dates: string[];
  trend: number[];
  seasonal: number[];
  residual: number[];
}

export interface CalendarHeatmap {
  years: number[];
  months: string[];
  z: number[][];
}

export interface StatisticsRequest extends VizFilterParams {
  parameter: string;
}

export interface StatisticsResponse {
  box_plot: BoxPlotSeries[];
  histogram: number[];
  annual_anomalies: AnnualAnomaly[];
  decomposition: Decomposition;
  calendar_heatmap: CalendarHeatmap;
  /**
   * False only when the selected dataset genuinely has no time dimension
   * at all. box_plot is NOT time-dependent (station/location-keyed) and
   * still has real data even when this is false — only the
   * histogram/annual_anomalies/decomposition/calendar_heatmap sections
   * (all date-bucketed) go empty.
   */
  has_temporal_data: boolean;
}

/** Sync-vs-Celery-job envelope (Visualize Performance plan, Phase 4) —
 * see ComparisonJobResponse/ComparisonJobStatus above for the full
 * reasoning, identical shape here. */
export interface StatisticsJobResponse {
  status: "complete" | "queued";
  job_id?: string | null;
  result?: StatisticsResponse | null;
}

export interface StatisticsJobStatus {
  id: string;
  status: string;
  result?: StatisticsResponse | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface ProfilePoint {
  depth_m: number;
  value: number;
}

export interface StationProfile {
  station: string | null;
  lat: number | null;
  lon: number | null;
  time: string | null;
  points: ProfilePoint[];
}

export interface TSPair {
  depth_m: number | null;
  temperature: number;
  salinity: number;
  lat: number | null;
  lon: number | null;
  time: string | null;
  station: string | null;
}

export interface ProfilesRequest extends VizFilterParams {
  parameter?: string | null;
  temperature_parameter?: string | null;
  salinity_parameter?: string | null;
}

export type DepthConvention = "assumed_positive_down" | "assumed_negative_up";

export interface ProfilesResponse {
  profiles: StationProfile[];
  ts_pairs: TSPair[];
  /** False only when the selected dataset genuinely has no depth/vertical dimension at all. */
  has_depth_data: boolean;
  depth_convention: DepthConvention | null;
}

export interface VizExportSettings {
  viz_export_temporal_enabled: boolean;
  viz_export_spatial_enabled: boolean;
  viz_export_comparison_enabled: boolean;
  viz_export_statistics_enabled: boolean;
}

export interface VizComputeLimits {
  viz_max_grid_resolution: number | null;
  viz_max_aoi_km2: number | null;
  viz_max_date_range_days_spatial: number | null;
  viz_max_date_range_days_timeseries: number | null;
  viz_max_date_range_days_comparison: number | null;
  viz_max_date_range_days_statistics: number | null;
}

export interface VisualizableDatasetSummary {
  id: string;
  code: string;
  title: string;
  variables: string[];
  /** null when this dataset has no temporal dimension at all. */
  temporal_start: string | null;
  temporal_end: string | null;
  /** null when this dataset has no recorded spatial extent at all. */
  spatial_extent: SpatialBounds | null;
}

export interface BoundaryShapefileSummary {
  id: string;
  name: string;
  geojson: GeoJSON.FeatureCollection;
  uploaded_at: string;
  is_default: boolean;
}
