import { apiFetch } from "./client";
import type { ReportCreate, ReportDownloadResponse, ReportPublic } from "@/lib/types/admin-reports";

export async function getReports(): Promise<ReportPublic[]> {
  return apiFetch<ReportPublic[]>("/admin/reports");
}

export async function getReport(id: string): Promise<ReportPublic> {
  return apiFetch<ReportPublic>(`/admin/reports/${id}`);
}

export async function createReport(payload: ReportCreate): Promise<ReportPublic> {
  return apiFetch<ReportPublic>("/admin/reports", { method: "POST", body: payload });
}

export async function getReportDownloadUrl(id: string): Promise<ReportDownloadResponse> {
  return apiFetch<ReportDownloadResponse>(`/admin/reports/${id}/download`);
}
