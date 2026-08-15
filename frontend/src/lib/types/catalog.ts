// Mirrors backend/app/schemas/catalog.py exactly — keep these two in sync.

export type DatasetSort = "relevance" | "title" | "updated" | "records";

export interface DatasetSummary {
  id: string;
  code: string;
  title: string;
  category: string | null;
  location: string | null;
  parameters: string[];
  source: string | null;
  platforms: string[];
  resolution: string | null;
  record_count: number;
  formats: string[];
  license: string | null;
  description: string | null;
  status: string;
  updated_at: string;
}

export interface CatalogSearchResponse {
  results: DatasetSummary[];
  total: number;
}

export interface SpatialBBox {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}

export interface DatasetDetail {
  id: string;
  code: string;
  title: string;
  category: string | null;
  location: string | null;
  parameters: string[];
  source: string | null;
  platforms: string[];
  resolution: string | null;
  record_count: number;
  formats: string[];
  processing_levels: string[];
  license: string | null;
  description: string | null;
  status: string;
  updated_at: string;
  temporal_start: string | null;
  temporal_end: string | null;
  spatial_bbox: SpatialBBox | null;
}

export interface StationOption {
  code: string;
  name: string;
  lat: number;
  lon: number;
}

export type QualityFlag = "normal" | "caution" | "alert";

export interface DatasetRecordPreview {
  id: string;
  time: string | null;
  location: string | null;
  depth_m: number | null;
  parameter: string;
  value: number;
  unit: string | null;
  platform: string | null;
  format: string | null;
  processing_level: string | null;
  quality_flag: QualityFlag;
}

export interface QualityBreakdown {
  normal: number;
  caution: number;
  alert: number;
}

export interface DatasetRecordsResponse {
  preview: DatasetRecordPreview[];
  matching_count: number;
  dataset_total_count: number;
  quality_breakdown: QualityBreakdown;
}

export interface TaxonomyOptions {
  categories: string[];
  parameters: string[];
  sources: string[];
  platforms: string[];
  formats: string[];
}

export interface SchemaFilterVariable {
  name: string;
  data_type: string;
  is_dimension: boolean;
  roles: string[];
  min_value: number | null;
  max_value: number | null;
  distinct_values: string[] | null;
}

export interface DatasetSchemaFilters {
  reviewed_at: string;
  variables: SchemaFilterVariable[];
}
