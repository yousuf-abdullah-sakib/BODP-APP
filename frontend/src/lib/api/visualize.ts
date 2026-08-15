import { apiFetch } from "./client";
import type {
  ComparisonRequest,
  ComparisonResponse,
  SpatialJobStatusResponse,
  SpatialRequest,
  SpatialResponse,
  StatisticsRequest,
  StatisticsResponse,
  TimeSeriesRequest,
  TimeSeriesResponse,
  VisualizableDatasetSummary,
  VizFilterParams,
} from "@/lib/types/visualize";
import type { StationOption } from "@/lib/types/catalog";

export async function getVisualizableDatasets(): Promise<VisualizableDatasetSummary[]> {
  return apiFetch<VisualizableDatasetSummary[]>("/visualize/datasets");
}

export async function postTimeseries(body: TimeSeriesRequest): Promise<TimeSeriesResponse> {
  return apiFetch<TimeSeriesResponse>("/visualize/timeseries", { method: "POST", body });
}

export async function postSpatial(body: SpatialRequest): Promise<SpatialResponse> {
  return apiFetch<SpatialResponse>("/visualize/spatial", { method: "POST", body });
}

export async function getSpatialJob(jobId: string): Promise<SpatialJobStatusResponse> {
  return apiFetch<SpatialJobStatusResponse>(`/visualize/spatial/${jobId}`);
}

export async function postComparison(body: ComparisonRequest): Promise<ComparisonResponse> {
  return apiFetch<ComparisonResponse>("/visualize/comparison", { method: "POST", body });
}

export async function postStatistics(body: StatisticsRequest): Promise<StatisticsResponse> {
  return apiFetch<StatisticsResponse>("/visualize/statistics", { method: "POST", body });
}

export async function postFilteredStations(body: VizFilterParams): Promise<StationOption[]> {
  return apiFetch<StationOption[]>("/visualize/stations", { method: "POST", body });
}
