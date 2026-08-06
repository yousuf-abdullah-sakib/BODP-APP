import { apiFetch } from "./client";
import type {
  ExtractionFormat,
  GrantSummary,
  RequestSummary,
  SearchCriteria,
  SubsetExtraction,
} from "@/lib/types/requests";

export interface SubmitRequestParams {
  datasetId: string;
  justification: string;
  searchCriteria?: SearchCriteria | null;
  file?: File | null;
}

export async function submitRequest(params: SubmitRequestParams): Promise<RequestSummary> {
  const form = new FormData();
  form.set("dataset_id", params.datasetId);
  form.set("justification", params.justification);
  if (params.searchCriteria) {
    form.set("search_criteria", JSON.stringify(params.searchCriteria));
  }
  if (params.file) {
    form.set("file", params.file);
  }
  return apiFetch<RequestSummary>("/requests", { method: "POST", body: form });
}

export async function getMyRequests(status?: string): Promise<RequestSummary[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<RequestSummary[]>(`/me/requests${qs}`);
}

export async function getMyGrants(): Promise<GrantSummary[]> {
  return apiFetch<GrantSummary[]>("/me/grants");
}

export interface StartExtractionParams {
  scope?: SearchCriteria;
  format: ExtractionFormat;
}

export async function startExtraction(
  grantId: string,
  params: StartExtractionParams
): Promise<SubsetExtraction> {
  return apiFetch<SubsetExtraction>(`/me/grants/${grantId}/extract`, {
    method: "POST",
    body: { scope: params.scope ?? {}, format: params.format },
  });
}

export async function getExtractionStatus(extractionId: string): Promise<SubsetExtraction> {
  return apiFetch<SubsetExtraction>(`/me/extractions/${extractionId}`);
}

export async function getExtractionDownloadUrl(extractionId: string): Promise<{ download_url: string }> {
  return apiFetch<{ download_url: string }>(`/me/extractions/${extractionId}/download`);
}
