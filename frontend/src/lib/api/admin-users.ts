import { apiFetch, apiUrl } from "./client";
import type { AdminUserCreate, AdminUserDetail, AdminUserSummary, AdminUserUpdate } from "@/lib/types/admin-users";
import type { GrantSummary } from "@/lib/types/requests";

export async function getAdminUsers(params?: {
  search?: string;
  status_filter?: string;
}): Promise<AdminUserSummary[]> {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.status_filter) qs.set("status_filter", params.status_filter);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<AdminUserSummary[]>(`/admin/users${suffix}`);
}

export async function getAdminUser(id: string): Promise<AdminUserDetail> {
  return apiFetch<AdminUserDetail>(`/admin/users/${id}`);
}

export async function createAdminUser(payload: AdminUserCreate): Promise<AdminUserDetail> {
  return apiFetch<AdminUserDetail>("/admin/users", { method: "POST", body: payload });
}

export async function updateAdminUser(id: string, payload: AdminUserUpdate): Promise<AdminUserDetail> {
  return apiFetch<AdminUserDetail>(`/admin/users/${id}`, { method: "PATCH", body: payload });
}

export async function suspendUser(id: string): Promise<AdminUserDetail> {
  return apiFetch<AdminUserDetail>(`/admin/users/${id}/suspend`, { method: "POST" });
}

export async function activateUser(id: string): Promise<AdminUserDetail> {
  return apiFetch<AdminUserDetail>(`/admin/users/${id}/activate`, { method: "POST" });
}

export async function getUserGrants(id: string): Promise<GrantSummary[]> {
  return apiFetch<GrantSummary[]>(`/admin/users/${id}/grants`);
}

export async function revokeUserGrant(userId: string, grantId: string): Promise<GrantSummary> {
  return apiFetch<GrantSummary>(`/admin/users/${userId}/grants/${grantId}/revoke`, { method: "POST" });
}

export async function assignRole(userId: string, roleId: string): Promise<void> {
  await apiFetch(`/admin/users/${userId}/roles/${roleId}`, { method: "POST" });
}

export async function unassignRole(userId: string, roleId: string): Promise<void> {
  await apiFetch(`/admin/users/${userId}/roles/${roleId}`, { method: "DELETE" });
}

// The CSV export endpoint returns raw text/csv with a Content-Disposition
// header, not JSON — apiFetch only understands application/json responses
// (see client.ts), so this bypasses it with a direct fetch + blob download,
// matching the client-side-CSV pattern already used in GrantsSection.tsx's
// exportCSV(), just sourcing the blob from a real endpoint instead of
// building it from in-memory data.
export async function exportUsersCsv(): Promise<void> {
  const token = typeof window !== "undefined" ? window.localStorage.getItem("bodp_access_token") : null;
  const res = await fetch(apiUrl("/admin/users/export.csv"), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("Failed to export users");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "bodp_users.csv";
  a.click();
  URL.revokeObjectURL(url);
}
