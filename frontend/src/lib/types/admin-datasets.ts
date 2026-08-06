// Mirrors backend/app/schemas/admin_datasets.py exactly — keep these two in sync.

export interface DatasetFilePublic {
  id: string;
  dataset_id: string;
  file_name: string;
  storage_backend: string;
  file_format: string | null;
  file_size_bytes: number | null;
  checksum: string | null;
  temporal_start: string | null;
  temporal_end: string | null;
  version: number;
  file_metadata: Record<string, unknown> | null;
}

export interface UploadStatusResponse {
  id: string;
  dataset_id: string | null;
  dataset_file_id: string | null;
  file_name: string;
  size_bytes: number | null;
  status: string;
  error_message: string | null;
  uploaded_at: string;
  celery_task_id: string | null;
}

export interface DatasetFileUploadResponse {
  upload: UploadStatusResponse;
  dataset_file: DatasetFilePublic;
}

export type DatasetStatus = "draft" | "published" | "archived";

export interface DatasetCreate {
  title: string;
  description?: string | null;
  code?: string | null;
  category_id?: string | null;
  location?: string | null;
  source?: string | null;
  platforms?: string[];
  parameters?: string[];
  resolution?: string | null;
  license?: string | null;
  processing_levels?: string[];
  status?: DatasetStatus;
}

export interface DatasetUpdate {
  title?: string;
  description?: string | null;
  category_id?: string | null;
  location?: string | null;
  source?: string | null;
  platforms?: string[] | null;
  parameters?: string[] | null;
  resolution?: string | null;
  license?: string | null;
  processing_levels?: string[] | null;
}

export interface DatasetAdminSummary {
  id: string;
  code: string;
  title: string;
  category_name: string | null;
  location: string | null;
  record_count: number;
  status: string;
  updated_at: string;
}

export interface DatasetAdminDetail {
  id: string;
  code: string;
  title: string;
  description: string | null;
  category_id: string | null;
  category_name: string | null;
  location: string | null;
  source: string | null;
  platforms: string[];
  parameters: string[];
  resolution: string | null;
  license: string | null;
  processing_levels: string[];
  formats: string[];
  status: string;
  temporal_start: string | null;
  temporal_end: string | null;
  record_count: number;
  created_at: string;
  updated_at: string;
  files: DatasetFilePublic[];
  active_grant_count: number;
}

export interface DatasetPermanentDeleteConfirm {
  confirm: true;
}
