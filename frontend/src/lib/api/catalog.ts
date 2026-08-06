import { apiFetch } from "./client";
import type {
  CatalogSearchResponse,
  DatasetDetail,
  DatasetRecordsResponse,
  DatasetSort,
  StationOption,
  TaxonomyOptions,
} from "@/lib/types/catalog";

export interface CatalogSearchParams {
  [key: string]: string | DatasetSort | undefined;
  search?: string;
  category?: string;
  parameter?: string;
  source?: string;
  platform?: string;
  format?: string;
  sort?: DatasetSort;
}

function buildQuery(params: Record<string, unknown>): string {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") usp.set(key, String(value));
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : "";
}

export async function searchCatalog(params: CatalogSearchParams): Promise<CatalogSearchResponse> {
  return apiFetch<CatalogSearchResponse>(`/catalog/search${buildQuery(params)}`, {
    skipAuth: true,
  });
}

export async function getCatalogTaxonomy(): Promise<TaxonomyOptions> {
  return apiFetch<TaxonomyOptions>("/catalog/taxonomy", { skipAuth: true });
}

export async function getDatasetDetail(id: string): Promise<DatasetDetail> {
  return apiFetch<DatasetDetail>(`/catalog/${id}`, { skipAuth: true });
}

export async function getDatasetStations(id: string): Promise<StationOption[]> {
  return apiFetch<StationOption[]>(`/catalog/${id}/stations`, { skipAuth: true });
}

export interface DatasetRecordsParams {
  [key: string]: string | number | undefined;
  parameter?: string;
  quality?: string;
  date_from?: string;
  date_to?: string;
  lat_min?: number;
  lat_max?: number;
  lon_min?: number;
  lon_max?: number;
  depth_min?: number;
  depth_max?: number;
  source?: string;
  platform?: string;
  station?: string;
  format?: string;
  processing_level?: string;
}

export async function getDatasetRecords(
  id: string,
  params: DatasetRecordsParams
): Promise<DatasetRecordsResponse> {
  return apiFetch<DatasetRecordsResponse>(`/catalog/${id}/records${buildQuery(params)}`, {
    skipAuth: true,
  });
}
