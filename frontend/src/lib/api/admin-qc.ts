import { apiFetch } from "./client";
import type { QcScanResult, QualityIssuePublic, QualityIssueStatus } from "@/lib/types/admin-qc";

export async function getQualityIssues(statusFilter?: string): Promise<QualityIssuePublic[]> {
  const qs = statusFilter ? `?status_filter=${encodeURIComponent(statusFilter)}` : "";
  return apiFetch<QualityIssuePublic[]>(`/admin/qc/issues${qs}`);
}

export async function runQcScan(): Promise<QcScanResult> {
  return apiFetch<QcScanResult>("/admin/qc/scan", { method: "POST" });
}

export async function updateIssueStatus(id: string, status: QualityIssueStatus): Promise<QualityIssuePublic> {
  return apiFetch<QualityIssuePublic>(`/admin/qc/issues/${id}`, { method: "PATCH", body: { status } });
}
