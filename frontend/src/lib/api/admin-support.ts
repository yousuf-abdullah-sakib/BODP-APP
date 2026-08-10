import { apiFetch } from "./client";
import type {
  SupportTicketAdminDetail,
  SupportTicketAdminSummary,
  SupportTicketReplyCreate,
} from "@/lib/types/admin-support";

export async function getSupportTicketsAdmin(params?: {
  status_filter?: string;
  search?: string;
}): Promise<SupportTicketAdminSummary[]> {
  const qs = new URLSearchParams();
  if (params?.status_filter) qs.set("status_filter", params.status_filter);
  if (params?.search) qs.set("search", params.search);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<SupportTicketAdminSummary[]>(`/admin/support-tickets${suffix}`);
}

export async function getSupportTicketAdmin(id: string): Promise<SupportTicketAdminDetail> {
  return apiFetch<SupportTicketAdminDetail>(`/admin/support-tickets/${id}`);
}

export async function replyToSupportTicket(
  id: string,
  payload: SupportTicketReplyCreate
): Promise<SupportTicketAdminDetail> {
  return apiFetch<SupportTicketAdminDetail>(`/admin/support-tickets/${id}/reply`, {
    method: "POST",
    body: payload,
  });
}
