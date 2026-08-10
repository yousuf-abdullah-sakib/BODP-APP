// Mirrors backend/app/schemas/me.py.

export interface ActivityItem {
  type: string;
  description: string;
  occurred_at: string;
}

export interface OverviewResponse {
  pending_requests: number;
  approved_requests: number;
  datasets_granted: number;
  total_downloads: number;
  datasets_viewed: number;
  total_extracted_bytes: number;
  member_since: string;
  recent_activity: ActivityItem[];
}

export interface ProfileDetail {
  id: string;
  email: string;
  full_name: string;
  role: string;
  institution?: string | null;
  phone?: string | null;
  bio?: string | null;
  research_area?: string | null;
  avatar_key?: string | null;
  datasets_granted: number;
  created_at: string;
  deletion_requested_at?: string | null;
}

export interface ProfileUpdate {
  full_name?: string;
  institution?: string;
  phone?: string;
  bio?: string;
  research_area?: string;
}

export interface SessionSummary {
  id: string;
  device: string | null;
  ip_address: string | null;
  location: string | null;
  user_agent: string | null;
  created_at: string;
  last_active_at: string;
  is_current: boolean;
}

export interface DeletionStatusResponse {
  deletion_requested_at: string | null;
  grace_period_days: number;
}

export interface PreferencesSchema {
  notify_request_status: boolean;
  notify_new_dataset: boolean;
  notify_weekly_digest: boolean;
  notify_security_alerts: boolean;
  notify_newsletter: boolean;
  date_format: string;
  coordinate_format: string;
  compact_table_rows: boolean;
}

export interface NotificationSummary {
  id: string;
  type: "success" | "warning" | "info" | "danger";
  title: string;
  description: string | null;
  unread: boolean;
  created_at: string;
}

export interface UnreadCountResponse {
  unread_count: number;
}

export interface SupportTicketCreate {
  subject: string;
  category?: string | null;
  priority?: string;
  message: string;
}

export interface SupportTicketSummary {
  id: string;
  subject: string;
  category: string | null;
  priority: string;
  status: string;
  message: string;
  reply_message: string | null;
  replied_at: string | null;
  created_at: string;
}

export interface CmsBlockSummary {
  key: string;
  page: string;
  label: string | null;
  value: string | null;
}
