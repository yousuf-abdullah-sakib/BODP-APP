"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { createReport, getReport, getReportDownloadUrl, getReports } from "@/lib/api/admin-reports";
import { REPORT_TYPES } from "@/lib/types/admin-reports";
import type { ReportPublic, ReportType } from "@/lib/types/admin-reports";

const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 30;

export default function ReportsSection() {
  const { toast } = useToast();
  const [reports, setReports] = useState<ReportPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [type, setType] = useState<ReportType>(REPORT_TYPES[0].type);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [generating, setGenerating] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function refetch() {
    setLoading(true);
    getReports()
      .then(setReports)
      .catch(() => toast("Failed to load reports.", "error"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pollUntilReady(reportId: string, attempt = 0) {
    if (attempt >= POLL_MAX_ATTEMPTS) return;
    pollTimer.current = setTimeout(async () => {
      try {
        const report = await getReport(reportId);
        setReports((prev) => prev.map((r) => (r.id === reportId ? report : r)));
        if (!report.ready) pollUntilReady(reportId, attempt + 1);
      } catch {
        // stop polling silently on error — the row still shows in the table
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleGenerate() {
    setGenerating(true);
    try {
      const report = await createReport({
        type,
        date_from: from || null,
        date_to: to || null,
      });
      toast(`${REPORT_TYPES.find((t) => t.type === type)?.label} report queued.`, "success");
      refetch();
      pollUntilReady(report.id);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to generate report.", "error");
    } finally {
      setGenerating(false);
    }
  }

  async function handleDownload(report: ReportPublic) {
    if (!report.ready) {
      toast("Report is still being generated — try again shortly.", "info");
      return;
    }
    try {
      const { download_url } = await getReportDownloadUrl(report.id);
      window.location.href = download_url;
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to download report.", "error");
    }
  }

  const latest = reports.find((r) => r.type === type);

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Reports</div>
          <div className="dash-sub">Generate and download system reports.</div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1.4rem" }}>
        <div className="panel-head">
          <span className="panel-title">Generate New Report</span>
        </div>
        <div className="panel-body">
          <div className="form-group">
            <label className="form-label">Report Type</label>
            <div className="report-type-grid">
              {REPORT_TYPES.map((t) => (
                <button
                  key={t.type}
                  type="button"
                  className={`report-type-card${type === t.type ? " active" : ""}`}
                  onClick={() => setType(t.type)}
                >
                  <span className="rtc-icon">{t.icon}</span>
                  <span className="rtc-title">{t.label}</span>
                  <span className="rtc-desc">{t.desc}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">From</label>
              <input className="form-input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">To</label>
              <input className="form-input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            </div>
          </div>
          <button className="btn-primary" onClick={handleGenerate} disabled={generating}>
            {generating ? "Queuing…" : "📊 Generate Report"}
          </button>
          {latest && (
            <div
              style={{
                marginTop: "1rem",
                padding: "0.8rem 1rem",
                borderRadius: 8,
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                fontSize: "0.82rem",
                color: "var(--text-secondary)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "1rem",
              }}
            >
              <span>
                {latest.ready ? "✓" : "⏳"} Latest: <b>{REPORT_TYPES.find((t) => t.type === latest.type)?.label}</b>{" "}
                ({latest.date_range}) — {latest.ready ? "ready" : "generating…"}
              </span>
              <button
                className="btn-icon-sm"
                title="Download"
                onClick={() => handleDownload(latest)}
                disabled={!latest.ready}
              >
                ⬇
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Report History</span>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          {loading ? (
            <div className="empty-state">
              <div className="es-icon">⏳</div>
              <p>Loading reports…</p>
            </div>
          ) : reports.length === 0 ? (
            <div className="empty-state">
              <div className="es-icon">📊</div>
              <p>No reports generated yet.</p>
            </div>
          ) : (
            <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Date Range</th>
                    <th>Generated</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {reports.map((r) => (
                    <tr key={r.id}>
                      <td style={{ fontWeight: 600 }}>{REPORT_TYPES.find((t) => t.type === r.type)?.label ?? r.type}</td>
                      <td style={{ fontSize: "0.82rem" }}>{r.date_range}</td>
                      <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                        {new Date(r.generated_at).toLocaleString()}
                      </td>
                      <td>
                        <span className={`badge ${r.ready ? "badge-approved" : "badge-pending"}`}>
                          {r.ready ? "Ready" : "Generating…"}
                        </span>
                      </td>
                      <td>
                        <button
                          className="btn-icon-sm"
                          title="Download"
                          onClick={() => handleDownload(r)}
                          disabled={!r.ready}
                        >
                          ⬇
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
