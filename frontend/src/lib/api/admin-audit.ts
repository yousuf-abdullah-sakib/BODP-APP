import { apiFetch, apiUrl } from "./client";
import type { AuditLogPage } from "@/lib/types/admin-audit";

export async function getAuditLog(params?: {
  search?: string;
  action_type?: string;
  page?: number;
  page_size?: number;
}): Promise<AuditLogPage> {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.action_type) qs.set("action_type", params.action_type);
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<AuditLogPage>(`/admin/audit-log${suffix}`);
}

// The export endpoint returns raw text/csv, not JSON — bypasses apiFetch
// with a direct fetch + blob download, matching exportUsersCsv()'s
// established pattern. Query params are passed through UNCHANGED from
// whatever filter state the caller currently has active on-screen — this
// is what makes export structurally match the visible list (the backend's
// shared _build_query() function guarantees the same rows either way).
export async function exportAuditLogCsv(params?: { search?: string; action_type?: string }): Promise<void> {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.action_type) qs.set("action_type", params.action_type);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";

  const token = typeof window !== "undefined" ? window.localStorage.getItem("bodp_access_token") : null;
  const res = await fetch(apiUrl(`/admin/audit-log/export${suffix}`), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Failed to export audit log");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "bodp_audit_log.csv";
  a.click();
  URL.revokeObjectURL(url);
}
