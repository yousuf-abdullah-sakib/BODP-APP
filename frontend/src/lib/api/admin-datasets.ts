import { apiFetch } from "./client";
import type {
  DatasetAdminDetail,
  DatasetAdminSummary,
  DatasetCreate,
  DatasetFileUploadResponse,
  DatasetUpdate,
  UploadStatusResponse,
} from "@/lib/types/admin-datasets";

export async function getAdminDatasets(params?: {
  search?: string;
  category_id?: string;
  status_filter?: string;
}): Promise<DatasetAdminSummary[]> {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.category_id) qs.set("category_id", params.category_id);
  if (params?.status_filter) qs.set("status_filter", params.status_filter);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<DatasetAdminSummary[]>(`/admin/datasets${suffix}`);
}

export async function getAdminDataset(id: string): Promise<DatasetAdminDetail> {
  return apiFetch<DatasetAdminDetail>(`/admin/datasets/${id}`);
}

export async function createDataset(payload: DatasetCreate): Promise<DatasetAdminDetail> {
  return apiFetch<DatasetAdminDetail>("/admin/datasets", { method: "POST", body: payload });
}

export async function updateDataset(id: string, payload: DatasetUpdate): Promise<DatasetAdminDetail> {
  return apiFetch<DatasetAdminDetail>(`/admin/datasets/${id}`, { method: "PATCH", body: payload });
}

export async function publishDataset(id: string): Promise<DatasetAdminDetail> {
  return apiFetch<DatasetAdminDetail>(`/admin/datasets/${id}/publish`, { method: "POST" });
}

export async function unpublishDataset(id: string): Promise<DatasetAdminDetail> {
  return apiFetch<DatasetAdminDetail>(`/admin/datasets/${id}/unpublish`, { method: "POST" });
}

export async function archiveDataset(id: string): Promise<DatasetAdminDetail> {
  return apiFetch<DatasetAdminDetail>(`/admin/datasets/${id}/archive`, { method: "POST" });
}

export async function unarchiveDataset(id: string): Promise<DatasetAdminDetail> {
  return apiFetch<DatasetAdminDetail>(`/admin/datasets/${id}/unarchive`, { method: "POST" });
}

export async function permanentlyDeleteDataset(id: string): Promise<void> {
  await apiFetch(`/admin/datasets/${id}`, { method: "DELETE", body: { confirm: true } });
}

export async function uploadDatasetFile(datasetId: string, file: File): Promise<DatasetFileUploadResponse> {
  const form = new FormData();
  form.set("file", file);
  return apiFetch<DatasetFileUploadResponse>(`/admin/datasets/${datasetId}/files`, {
    method: "POST",
    body: form,
  });
}

export async function getUploadStatus(uploadId: string): Promise<UploadStatusResponse> {
  return apiFetch<UploadStatusResponse>(`/admin/datasets/uploads/${uploadId}`);
}
