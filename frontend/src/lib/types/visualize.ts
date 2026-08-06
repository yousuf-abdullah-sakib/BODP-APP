// Mirrors backend/app/schemas/visualize.py.

export interface VizFilterParams {
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
  value: number;
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

export type InterpolationMethod = "idw" | "kriging" | "nearest";

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
}

export interface ScatterResult {
  x_parameter: string;
  y_parameter: string;
  x: number[];
  y: number[];
  r: number;
  regression: RegressionResult;
}

export interface CorrelationMatrix {
  parameters: string[];
  matrix: number[][];
}

export interface ComparisonRequest extends VizFilterParams {
  parameters: string[];
}

export interface ComparisonResponse {
  scatter: ScatterResult;
  series_by_parameter: Record<string, SeriesPoint[]>;
  correlation_matrix: CorrelationMatrix;
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
}

export interface VizExportSettings {
  viz_export_temporal_enabled: boolean;
  viz_export_spatial_enabled: boolean;
  viz_export_comparison_enabled: boolean;
  viz_export_statistics_enabled: boolean;
}

export interface BoundaryShapefileSummary {
  id: string;
  name: string;
  geojson: GeoJSON.FeatureCollection;
  uploaded_at: string;
  is_default: boolean;
}
