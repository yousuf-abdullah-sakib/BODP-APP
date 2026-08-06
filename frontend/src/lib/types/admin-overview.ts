// Mirrors backend/app/schemas/admin_overview.py exactly — keep these two in sync.

export interface StatCardValue {
  current: number;
  previous: number | null;
  delta_pct: number | null;
  sparkline: number[];
}

export interface ActivityEntry {
  description: string;
  occurred_at: string;
}

export interface RequestPreview {
  id: string;
  dataset_title: string;
  requester_name: string;
  submitted_at: string;
}

export interface StorageUsageSchema {
  used_bytes: number;
  capacity_bytes: number | null;
  percent_used: number | null;
}

export interface SystemHealthSchema {
  database: boolean;
  redis: boolean;
  storage: boolean;
  checked_at: string;
}

export interface TopDownloadedEntry {
  dataset_title: string;
  count: number;
}

export interface AdminOverviewResponse {
  total_users: StatCardValue;
  active_users: StatCardValue;
  total_datasets: StatCardValue;
  downloads: StatCardValue;
  pending_requests: StatCardValue;
  recent_requests: RequestPreview[];
  recent_activity: ActivityEntry[];
  category_distribution: Record<string, number>;
  storage: StorageUsageSchema;
  top_downloaded: TopDownloadedEntry[];
  system_health: SystemHealthSchema;
}

export interface StorageCapacitySchema {
  storage_capacity_bytes: number | null;
}

export interface StorageCapacityUpdate {
  storage_capacity_bytes?: number | null;
}
