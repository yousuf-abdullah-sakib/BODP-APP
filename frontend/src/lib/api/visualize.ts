import { apiFetch } from "./client";
import type {
  ComparisonJobResponse,
  ComparisonJobStatus,
  ComparisonRequest,
  CoverageRequest,
  CoverageResponse,
  ProfilesRequest,
  ProfilesResponse,
  SpatialJobStatusResponse,
  SpatialRequest,
  SpatialResponse,
  StatisticsJobResponse,
  StatisticsJobStatus,
  StatisticsRequest,
  TimeSeriesRequest,
  TimeSeriesResponse,
  VisualizableDatasetSummary,
  VizFilterParams,
} from "@/lib/types/visualize";
import type { StationOption } from "@/lib/types/catalog";

export async function getVisualizableDatasets(): Promise<VisualizableDatasetSummary[]> {
  return apiFetch<VisualizableDatasetSummary[]>("/visualize/datasets");
}

export async function postTimeseries(
  body: TimeSeriesRequest,
  options?: { signal?: AbortSignal }
): Promise<TimeSeriesResponse> {
  return apiFetch<TimeSeriesResponse>("/visualize/timeseries", { method: "POST", body, ...options });
}

export async function postSpatial(body: SpatialRequest): Promise<SpatialResponse> {
  return apiFetch<SpatialResponse>("/visualize/spatial", { method: "POST", body });
}

export async function getSpatialJob(jobId: string): Promise<SpatialJobStatusResponse> {
  return apiFetch<SpatialJobStatusResponse>(`/visualize/spatial/${jobId}`);
}

export async function postComparison(
  body: ComparisonRequest,
  options?: { signal?: AbortSignal }
): Promise<ComparisonJobResponse> {
  return apiFetch<ComparisonJobResponse>("/visualize/comparison", { method: "POST", body, ...options });
}

export async function getComparisonJob(jobId: string): Promise<ComparisonJobStatus> {
  return apiFetch<ComparisonJobStatus>(`/visualize/comparison/${jobId}`);
}

export async function postStatistics(
  body: StatisticsRequest,
  options?: { signal?: AbortSignal }
): Promise<StatisticsJobResponse> {
  return apiFetch<StatisticsJobResponse>("/visualize/statistics", { method: "POST", body, ...options });
}

export async function getStatisticsJob(jobId: string): Promise<StatisticsJobStatus> {
  return apiFetch<StatisticsJobStatus>(`/visualize/statistics/${jobId}`);
}

export async function postProfiles(
  body: ProfilesRequest,
  options?: { signal?: AbortSignal }
): Promise<ProfilesResponse> {
  return apiFetch<ProfilesResponse>("/visualize/profiles", { method: "POST", body, ...options });
}

export async function postFilteredStations(body: VizFilterParams): Promise<StationOption[]> {
  return apiFetch<StationOption[]>("/visualize/stations", { method: "POST", body });
}

export async function postCoverage(body: CoverageRequest): Promise<CoverageResponse> {
  return apiFetch<CoverageResponse>("/visualize/coverage", { method: "POST", body });
}
