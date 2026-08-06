// Mirrors backend/app/schemas/admin_reports.py exactly — keep these two in sync.

export type ReportType = "usage_summary" | "dataset_inventory" | "user_activity" | "access_grants";

export interface ReportCreate {
  type: ReportType;
  date_from?: string | null;
  date_to?: string | null;
}

export interface ReportPublic {
  id: string;
  type: string;
  date_range: string | null;
  generated_by: string | null;
  generated_at: string;
  celery_task_id: string | null;
  ready: boolean;
}

export interface ReportDownloadResponse {
  download_url: string;
}

export const REPORT_TYPES: { type: ReportType; label: string; icon: string; desc: string }[] = [
  { type: "usage_summary", label: "Usage Summary", icon: "📈", desc: "Requests, approvals, and downloads over the selected period." },
  { type: "dataset_inventory", label: "Dataset Inventory", icon: "🗄️", desc: "Full catalog snapshot — status, category, record counts." },
  { type: "user_activity", label: "User Activity", icon: "👥", desc: "New registrations, logins, and account status changes." },
  { type: "access_grants", label: "Access Grants", icon: "🔑", desc: "Active, expiring, and revoked dataset access grants." },
];
