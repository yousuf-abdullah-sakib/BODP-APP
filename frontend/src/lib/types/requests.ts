// Mirrors backend/app/schemas/requests.py exactly — keep these two in sync.

export type GrantDuration = "5d" | "10d" | "1m" | "2m" | "6m" | "1y" | "custom";

export interface SpatialBounds {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}

export interface SearchCriteria {
  category?: string | null;
  // Checkbox multi-select — zero/omitted means "all approved parameters"
  // (no filtering), one or more means restrict to exactly those values.
  parameters?: string[] | null;
  source?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  bounds?: SpatialBounds | null;
}

export interface RequestDatasetSummary {
  id: string;
  code: string;
  title: string;
  category: string | null;
}

export interface RequestUserSummary {
  id: string;
  full_name: string;
  email: string;
  institution: string | null;
}

export interface RequestSummary {
  id: string;
  dataset: RequestDatasetSummary;
  justification: string;
  search_criteria: SearchCriteria | null;
  status: "pending" | "approved" | "rejected";
  submitted_at: string;
  reviewed_at: string | null;
  admin_note: string | null;
}

export interface RequestDetail extends RequestSummary {
  user: RequestUserSummary;
  // The admin's in-progress edited filter configuration (Save Changes),
  // separate from search_criteria (the immutable original). Null until an
  // admin has actually modified and saved this request's scope.
  admin_modified_search_criteria: SearchCriteria | null;
}

export interface GrantSummary {
  id: string;
  dataset: RequestDatasetSummary;
  granted_at: string;
  expires_at: string;
  status: "active" | "revoked" | "expired";
  scope: SearchCriteria | null;
}

export interface GrantDetail extends GrantSummary {
  user: RequestUserSummary;
  granted_by_name: string | null;
}

export type ExtractionFormat = "csv" | "parquet" | "netcdf" | "mat";

export type ExtractionStatus = "queued" | "processing" | "complete" | "failed";

export interface SubsetExtraction {
  id: string;
  grant_id: string;
  status: ExtractionStatus;
  format: string | null;
  requested_scope: SearchCriteria | null;
  output_size_bytes: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  celery_task_id: string | null;
}
