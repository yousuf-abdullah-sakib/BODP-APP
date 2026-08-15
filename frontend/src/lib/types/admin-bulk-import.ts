// Mirrors backend/app/schemas/admin_bulk_import.py exactly — keep these two in sync.

import type { UploadStatusResponse } from "./admin-datasets";

export interface BulkImportValidateRequest {
  dataset_id: string;
  source_path: string;
}

export interface BulkImportValidateResponse {
  dataset_id: string;
  dataset_title: string;
  source_path: string;
  filename: string;
  size_bytes: number;
  extension: string;
  target_bucket: string;
  target_key: string;
}

export interface BulkImportStartRequest {
  dataset_id: string;
  source_path: string;
}

export interface BulkImportStartResponse {
  upload: UploadStatusResponse;
}
