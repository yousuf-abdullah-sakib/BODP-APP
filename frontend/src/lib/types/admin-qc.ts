// Mirrors backend/app/schemas/admin_qc.py exactly — keep these two in sync.

export interface QualityIssuePublic {
  id: string;
  dataset_id: string;
  dataset_title: string;
  issue_type: string;
  severity: string;
  status: string;
  detail: string | null;
  detected_at: string;
}

export interface QcScanResult {
  issues_found: number;
  issues: QualityIssuePublic[];
}

export type QualityIssueStatus = "resolved" | "ignored";

export interface QualityIssueStatusUpdate {
  status: QualityIssueStatus;
}
