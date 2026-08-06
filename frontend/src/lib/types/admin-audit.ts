// Mirrors backend/app/schemas/admin_audit.py exactly — keep these two in sync.

export type AuditActionType = "approve" | "reject" | "revoke" | "user" | "dataset" | "content" | "login";

export interface AuditLogEntryPublic {
  id: string;
  actor_id: string | null;
  actor_name: string | null;
  action: string;
  action_type: AuditActionType;
  target: string | null;
  ip_address: string | null;
  created_at: string;
}

export interface AuditLogPage {
  items: AuditLogEntryPublic[];
  total: number;
  page: number;
  page_size: number;
}

export const TYPE_OPTIONS: { value: "" | AuditActionType; label: string }[] = [
  { value: "", label: "All Actions" },
  { value: "approve", label: "Approvals" },
  { value: "reject", label: "Rejections" },
  { value: "revoke", label: "Revocations" },
  { value: "user", label: "User changes" },
  { value: "dataset", label: "Dataset changes" },
  { value: "content", label: "Content changes" },
  { value: "login", label: "Logins" },
];

export const TYPE_STYLE: Record<AuditActionType, { color: string; icon: string }> = {
  approve: { color: "var(--stat-green)", icon: "✓" },
  reject: { color: "var(--stat-red)", icon: "✗" },
  revoke: { color: "var(--stat-orange)", icon: "⛔" },
  user: { color: "var(--stat-blue)", icon: "👤" },
  dataset: { color: "var(--stat-purple)", icon: "🗄️" },
  content: { color: "#0891b2", icon: "📰" },
  login: { color: "var(--stat-cyan)", icon: "🔑" },
};
