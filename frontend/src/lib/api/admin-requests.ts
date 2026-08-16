import { apiFetch } from "./client";
import type { GrantDetail, GrantDuration, RequestDetail, SearchCriteria } from "@/lib/types/requests";

export async function getAdminRequests(status?: string): Promise<RequestDetail[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<RequestDetail[]>(`/admin/requests${qs}`);
}

export interface ApproveRequestParams {
  duration: GrantDuration;
  customExpiresAt?: string;
  searchCriteria?: SearchCriteria | null;
  note?: string;
}

export async function approveRequest(
  requestId: string,
  params: ApproveRequestParams
): Promise<GrantDetail> {
  return apiFetch<GrantDetail>(`/admin/requests/${requestId}/approve`, {
    method: "POST",
    body: {
      duration: params.duration,
      custom_expires_at: params.customExpiresAt ?? null,
      search_criteria: params.searchCriteria ?? null,
      note: params.note ?? null,
    },
  });
}

export async function rejectRequest(requestId: string, reason: string): Promise<RequestDetail> {
  return apiFetch<RequestDetail>(`/admin/requests/${requestId}/reject`, {
    method: "POST",
    body: { reason },
  });
}

export async function getAdminGrants(status?: string): Promise<GrantDetail[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<GrantDetail[]>(`/admin/grants${qs}`);
}

export async function extendGrant(
  grantId: string,
  duration: GrantDuration,
  customExpiresAt?: string
): Promise<GrantDetail> {
  return apiFetch<GrantDetail>(`/admin/grants/${grantId}/extend`, {
    method: "POST",
    body: { duration, custom_expires_at: customExpiresAt ?? null },
  });
}

export async function revokeGrant(grantId: string): Promise<GrantDetail> {
  return apiFetch<GrantDetail>(`/admin/grants/${grantId}/revoke`, { method: "POST" });
}
