import { apiFetch } from "./client";
import type {
  DatasetSchemaDetail,
  DatasetSchemaReviewResult,
  DatasetSchemaReviewSummary,
  DatasetVariablePublic,
} from "@/lib/types/admin-dataset-schema";

export async function getDatasetsForReview(unreviewedOnly?: boolean): Promise<DatasetSchemaReviewSummary[]> {
  const qs = unreviewedOnly ? "?unreviewed_only=true" : "";
  return apiFetch<DatasetSchemaReviewSummary[]>(`/admin/dataset-schema${qs}`);
}

export async function getDatasetSchema(datasetId: string): Promise<DatasetSchemaDetail> {
  return apiFetch<DatasetSchemaDetail>(`/admin/dataset-schema/${datasetId}`);
}

export async function updateVariableRoles(
  datasetId: string,
  variableId: string,
  roles: string[]
): Promise<DatasetVariablePublic> {
  return apiFetch<DatasetVariablePublic>(`/admin/dataset-schema/${datasetId}/variables/${variableId}`, {
    method: "PATCH",
    body: { roles },
  });
}

export async function markDatasetReviewed(datasetId: string): Promise<DatasetSchemaReviewResult> {
  return apiFetch<DatasetSchemaReviewResult>(`/admin/dataset-schema/${datasetId}/mark-reviewed`, {
    method: "POST",
  });
}
