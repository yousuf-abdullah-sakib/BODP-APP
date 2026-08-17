// Mirrors backend/app/schemas/admin_dataset_schema.py exactly — keep these two in sync.

export type VariableRole = "dimension" | "data_variable" | "visualization_variable";

export const VARIABLE_ROLE_LABELS: Record<VariableRole, string> = {
  dimension: "Dimension / Filter",
  data_variable: "Data Variable",
  visualization_variable: "Visualization Variable",
};

export interface DatasetVariablePublic {
  id: string;
  dataset_id: string;
  name: string;
  data_type: string;
  unit: string | null;
  is_dimension: boolean;
  roles: string[];
  min_value: number | null;
  max_value: number | null;
  distinct_values: string[] | null;
  detected_at: string;
}

export interface DatasetSchemaReviewSummary {
  dataset_id: string;
  dataset_code: string;
  dataset_title: string;
  variable_count: number;
  unassigned_count: number;
  schema_reviewed_at: string | null;
  schema_reviewed_by_name: string | null;
}

// PLAN.md Phase 5 — how a DatasetFile's actual observation values are
// stored/queried. Nullable: a pre-Phase-5 file not yet through the
// best-effort backfill script shows null, never a fabricated guess.
export type StorageKind = "row_records" | "parquet" | "chunked_array" | "raster" | null;

export const STORAGE_KIND_LABELS: Record<NonNullable<StorageKind>, string> = {
  row_records: "Database Rows (legacy)",
  parquet: "Parquet",
  chunked_array: "Zarr (chunked array)",
  raster: "Raster (COG)",
};

export interface DatasetFileStorageSummary {
  id: string;
  file_name: string;
  file_format: string | null;
  storage_kind: StorageKind;
  uploaded_at: string | null;
}

export interface DatasetSchemaDetail {
  dataset_id: string;
  dataset_code: string;
  dataset_title: string;
  schema_reviewed_at: string | null;
  schema_reviewed_by_name: string | null;
  variables: DatasetVariablePublic[];
  files: DatasetFileStorageSummary[];
}

export interface DatasetSchemaReviewResult {
  dataset_id: string;
  schema_reviewed_at: string;
  schema_reviewed_by_name: string | null;
}

export interface VariableRoleUpdate {
  roles: string[];
}
