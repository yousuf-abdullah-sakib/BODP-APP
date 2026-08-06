// Mirrors backend/app/schemas/admin_analytics.py exactly — keep these two in sync.

export interface DailyCount {
  date: string;
  count: number;
}

export interface CategoryCount {
  category: string;
  count: number;
}

export interface TopDataset {
  title: string;
  count: number;
}

export interface AnalyticsResponse {
  requests_submitted: number;
  approval_rate_pct: number;
  total_downloads: number;
  new_researchers: number;
  requests_over_time: DailyCount[];
  downloads_over_time: DailyCount[];
  new_users_over_time: DailyCount[];
  requests_by_status: Record<string, number>;
  requests_by_category: CategoryCount[];
  top_datasets_by_grants: TopDataset[];
}
