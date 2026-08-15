import { apiFetch } from "./client";
import type {
  BulkImportStartRequest,
  BulkImportStartResponse,
  BulkImportValidateRequest,
  BulkImportValidateResponse,
} from "@/lib/types/admin-bulk-import";

export async function validateBulkImport(
  body: BulkImportValidateRequest
): Promise<BulkImportValidateResponse> {
  return apiFetch<BulkImportValidateResponse>("/admin/bulk-import/validate", {
    method: "POST",
    body,
  });
}

export async function startBulkImport(body: BulkImportStartRequest): Promise<BulkImportStartResponse> {
  return apiFetch<BulkImportStartResponse>("/admin/bulk-import", { method: "POST", body });
}
