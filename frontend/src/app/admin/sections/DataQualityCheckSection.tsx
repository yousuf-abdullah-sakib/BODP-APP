"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getQualityIssues, runQcScan, updateIssueStatus } from "@/lib/api/admin-qc";
import type { QualityIssuePublic } from "@/lib/types/admin-qc";

const SEVERITY_BADGE: Record<string, string> = { low: "badge-approved", medium: "badge-caution", high: "badge-alert" };
const STATUS_BADGE: Record<string, string> = { open: "badge-pending", resolved: "badge-approved", ignored: "badge-revoked" };

export default function DataQualityCheckSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [issues, setIssues] = useState<QualityIssuePublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"" | "open" | "resolved" | "ignored">("open");
  const [scanning, setScanning] = useState(false);

  function refetch() {
    setLoading(true);
    getQualityIssues()
      .then(setIssues)
      .catch(() => setError("Failed to load quality issues."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
  }, []);

  const filtered = filter ? issues.filter((i) => i.status === filter) : issues;

  async function handleScan() {
    setScanning(true);
    try {
      const result = await runQcScan();
      if (result.issues_found > 0) {
        toast(`QC scan complete — ${result.issues_found} new issue(s) found.`, "info");
      } else {
        toast("QC scan complete — no new issues found.", "success");
      }
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "QC scan failed.", "error");
    } finally {
      setScanning(false);
    }
  }

  async function handleResolve(issue: QualityIssuePublic) {
    try {
      await updateIssueStatus(issue.id, "resolved");
      toast("Issue marked as resolved.", "success");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to update issue.", "error");
    }
  }

  async function handleIgnore(issue: QualityIssuePublic) {
    const ok = await confirm({
      title: "Ignore Quality Issue",
      message: (
        <>
          Mark this issue on <b>{issue.dataset_title}</b> as ignored? It will remain in the log but no longer count
          as open.
        </>
      ),
      confirmLabel: "Ignore",
      danger: false,
    });
    if (!ok) return;
    try {
      await updateIssueStatus(issue.id, "ignored");
      toast("Issue marked as ignored.", "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to update issue.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Data Quality Check</div>
          <div className="dash-sub">Review and resolve automated data quality flags.</div>
        </div>
        <button className="btn-primary" onClick={handleScan} disabled={scanning}>
          {scanning ? "Scanning…" : "🔍 Run QC Scan"}
        </button>
      </div>

      <div className="filter-tab-row">
        {(["", "open", "resolved", "ignored"] as const).map((s) => (
          <button key={s} className={`filter-tab-btn${filter === s ? " active" : ""}`} onClick={() => setFilter(s)}>
            {s === "" ? "All" : s[0].toUpperCase() + s.slice(1)} (
            {s === "" ? issues.length : issues.filter((i) => i.status === s).length})
          </button>
        ))}
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading quality issues…</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
          {filtered.length === 0 && (
            <div className="empty-state">
              <div className="es-icon">✅</div>
              <p>No issues in this category.</p>
            </div>
          )}
          {filtered.map((issue) => (
            <div className="panel" key={issue.id}>
              <div className="panel-head">
                <div>
                  <span className="panel-title">{issue.dataset_title}</span>{" "}
                  <span className="chip">{issue.issue_type}</span>
                </div>
                <div style={{ display: "flex", gap: "0.4rem" }}>
                  <span className={`badge ${SEVERITY_BADGE[issue.severity] ?? "badge-caution"}`}>{issue.severity}</span>
                  <span className={`badge ${STATUS_BADGE[issue.status] ?? "badge-pending"}`}>{issue.status}</span>
                </div>
              </div>
              <div className="panel-body">
                <p
                  style={{
                    fontSize: "0.82rem",
                    color: "var(--text-secondary)",
                    marginBottom: issue.status === "open" ? "0.9rem" : 0,
                  }}
                >
                  {issue.detail}
                </p>
                <div
                  style={{
                    fontSize: "0.72rem",
                    color: "var(--text-muted)",
                    marginBottom: issue.status === "open" ? "0.9rem" : 0,
                  }}
                >
                  Detected {new Date(issue.detected_at).toLocaleString()}
                </div>
                {issue.status === "open" && (
                  <div style={{ display: "flex", gap: "0.5rem" }}>
                    <button className="btn-success-sm" onClick={() => handleResolve(issue)}>
                      ✓ Mark Resolved
                    </button>
                    <button className="btn-ghost-sm" onClick={() => handleIgnore(issue)}>
                      Ignore
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
