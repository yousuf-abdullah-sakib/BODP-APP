import { apiFetch } from "./client";
import type { AnalyticsResponse } from "@/lib/types/admin-analytics";

export async function getAnalytics(params?: {
  date_from?: string;
  date_to?: string;
}): Promise<AnalyticsResponse> {
  const qs = new URLSearchParams();
  if (params?.date_from) qs.set("date_from", params.date_from);
  if (params?.date_to) qs.set("date_to", params.date_to);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<AnalyticsResponse>(`/admin/analytics${suffix}`);
}
