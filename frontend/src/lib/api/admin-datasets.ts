import { apiFetch } from "./client";
import type {
  DatasetAdminDetail,
  DatasetAdminSummary,
  DatasetCreate,
  DatasetFileUploadResponse,
  DatasetUpdate,
  MultipartUploadCompleteResponse,
  MultipartUploadInitiateResponse,
  MultipartUploadPresignPartResponse,
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

// --- Direct-to-MinIO multipart upload (large files) ---
//
// Only the four control-plane calls below (initiate / presign-part /
// mark-part-complete / complete) go through apiFetch — the actual file
// bytes never touch this backend. Each part's PUT goes straight to the
// presigned MinIO URL via a raw fetch(), deliberately bypassing apiFetch:
// no bearer token (the presigned URL's signature IS the auth), no
// Content-Type/JSON handling, and the request body is a raw Blob slice.

export async function initiateMultipartUpload(
  datasetId: string,
  filename: string,
  totalSizeBytes: number
): Promise<MultipartUploadInitiateResponse> {
  return apiFetch<MultipartUploadInitiateResponse>(`/admin/datasets/${datasetId}/uploads/initiate`, {
    method: "POST",
    body: { filename, total_size_bytes: totalSizeBytes },
  });
}

export async function presignUploadPart(
  uploadId: string,
  partNumber: number
): Promise<MultipartUploadPresignPartResponse> {
  return apiFetch<MultipartUploadPresignPartResponse>(
    `/admin/datasets/uploads/${uploadId}/parts/${partNumber}/presign`,
    { method: "POST" }
  );
}

/** PUTs one part's bytes directly to MinIO via its presigned URL and
 * returns the ETag MinIO assigned it — required later to complete the
 * multipart upload. Does not go through apiFetch/API_BASE_URL: the
 * presigned URL is already a complete, absolute, pre-authorized address. */
export async function uploadPartDirect(uploadUrl: string, blob: Blob): Promise<string> {
  const res = await fetch(uploadUrl, { method: "PUT", body: blob });
  if (!res.ok) {
    throw new Error(`Failed to upload part (status ${res.status})`);
  }
  const etag = res.headers.get("ETag");
  if (!etag) {
    throw new Error("MinIO did not return an ETag for the uploaded part");
  }
  return etag.replaceAll('"', "");
}

export async function markPartUploaded(
  uploadId: string,
  partNumber: number,
  sizeBytes: number
): Promise<UploadStatusResponse> {
  return apiFetch<UploadStatusResponse>(`/admin/datasets/uploads/${uploadId}/parts/complete`, {
    method: "POST",
    body: { part_number: partNumber, size_bytes: sizeBytes },
  });
}

export async function completeMultipartUpload(uploadId: string): Promise<MultipartUploadCompleteResponse> {
  return apiFetch<MultipartUploadCompleteResponse>(`/admin/datasets/uploads/${uploadId}/complete`, {
    method: "POST",
  });
}

export async function cancelUpload(uploadId: string): Promise<UploadStatusResponse> {
  return apiFetch<UploadStatusResponse>(`/admin/datasets/uploads/${uploadId}/cancel`, {
    method: "POST",
  });
}
