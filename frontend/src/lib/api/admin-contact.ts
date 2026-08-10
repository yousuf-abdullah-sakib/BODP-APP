import { apiFetch } from "./client";
import type {
  ContactReplyCreate,
  ContactSubmissionAdminDetail,
  ContactSubmissionAdminSummary,
} from "@/lib/types/admin-contact";

export async function getContactSubmissions(params?: {
  status_filter?: string;
  search?: string;
}): Promise<ContactSubmissionAdminSummary[]> {
  const qs = new URLSearchParams();
  if (params?.status_filter) qs.set("status_filter", params.status_filter);
  if (params?.search) qs.set("search", params.search);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<ContactSubmissionAdminSummary[]>(`/admin/contact${suffix}`);
}

export async function getContactSubmission(id: string): Promise<ContactSubmissionAdminDetail> {
  return apiFetch<ContactSubmissionAdminDetail>(`/admin/contact/${id}`);
}

export async function replyToContactSubmission(
  id: string,
  payload: ContactReplyCreate
): Promise<ContactSubmissionAdminDetail> {
  return apiFetch<ContactSubmissionAdminDetail>(`/admin/contact/${id}/reply`, {
    method: "POST",
    body: payload,
  });
}
