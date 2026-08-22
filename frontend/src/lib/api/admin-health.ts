import { apiFetch } from "./client";
import type { DetailedHealthResponse } from "@/lib/types/admin-health";

export async function getDetailedHealth(): Promise<DetailedHealthResponse> {
  return apiFetch<DetailedHealthResponse>("/admin/health");
}
