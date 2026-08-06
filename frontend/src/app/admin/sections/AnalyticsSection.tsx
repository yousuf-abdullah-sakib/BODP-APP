"use client";

import { useEffect, useState } from "react";
import PlotlyChart from "@/components/charts/PlotlyChart";
import { getAnalytics } from "@/lib/api/admin-analytics";
import type { AnalyticsResponse } from "@/lib/types/admin-analytics";
import type { Data } from "plotly.js";

const RANGE_OPTIONS = [
  { key: "30d", label: "Last 30 Days", days: 30 },
  { key: "90d", label: "Last 90 Days", days: 90 },
  { key: "1y", label: "Last 12 Months", days: 365 },
];

export default function AnalyticsSection() {
  const [range, setRange] = useState(RANGE_OPTIONS[2]);
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    const dateTo = new Date();
    const dateFrom = new Date(dateTo.getTime() - range.days * 86400000);
    getAnalytics({ date_from: dateFrom.toISOString(), date_to: dateTo.toISOString() })
      .then(setData)
      .catch(() => setError("Failed to load analytics."))
      .finally(() => setLoading(false));
  }, [range]);

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Analytics</div>
          <div className="dash-sub">Platform-wide usage and growth metrics.</div>
        </div>
        <select
          className="form-select"
          style={{ maxWidth: 200 }}
          value={range.key}
          onChange={(e) => setRange(RANGE_OPTIONS.find((o) => o.key === e.target.value) ?? RANGE_OPTIONS[2])}
        >
          {RANGE_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading || !data ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading analytics…</p>
        </div>
      ) : (
        <>
          <div className="stat-row">
            <div className="stat-card blue">
              <div className="stat-card-label">Requests Submitted</div>
              <div className="stat-card-num">{data.requests_submitted}</div>
            </div>
            <div className="stat-card green">
              <div className="stat-card-label">Approval Rate</div>
              <div className="stat-card-num">{data.approval_rate_pct}%</div>
            </div>
            <div className="stat-card orange">
              <div className="stat-card-label">Total Downloads</div>
              <div className="stat-card-num">{data.total_downloads.toLocaleString()}</div>
            </div>
            <div className="stat-card purple">
              <div className="stat-card-label">New Researchers</div>
              <div className="stat-card-num">{data.new_researchers}</div>
            </div>
          </div>

          <div className="panel-grid-2">
            <div className="chart-container" style={{ marginBottom: 0 }}>
              <div className="chart-head">
                <div className="chart-title">New Researchers Over Time</div>
              </div>
              <div className="chart-body">
                {data.new_users_over_time.length === 0 ? (
                  <div style={{ fontSize: "0.82rem", color: "var(--text-muted)", padding: "2rem 0" }}>
                    No new registrations in this period.
                  </div>
                ) : (
                  <PlotlyChart
                    height={260}
                    data={[
                      {
                        x: data.new_users_over_time.map((d) => d.date),
                        y: data.new_users_over_time.map((d) => d.count),
                        type: "scatter",
                        mode: "lines",
                        fill: "tozeroy",
                      } as Data,
                    ]}
                  />
                )}
              </div>
            </div>
            <div className="chart-container" style={{ marginBottom: 0 }}>
              <div className="chart-head">
                <div className="chart-title">Requests by Status</div>
              </div>
              <div className="chart-body">
                {Object.keys(data.requests_by_status).length === 0 ? (
                  <div style={{ fontSize: "0.82rem", color: "var(--text-muted)", padding: "2rem 0" }}>
                    No requests in this period.
                  </div>
                ) : (
                  <PlotlyChart
                    height={260}
                    data={[
                      {
                        labels: Object.keys(data.requests_by_status),
                        values: Object.values(data.requests_by_status),
                        type: "pie",
                        hole: 0.55,
                        marker: { colors: ["#ffc857", "#4ecb8d", "#ff6b5b"] },
                      } as Data,
                    ]}
                  />
                )}
              </div>
            </div>
          </div>

          <div className="panel-grid-2">
            <div className="chart-container" style={{ marginBottom: 0 }}>
              <div className="chart-head">
                <div className="chart-title">Most Requested Categories</div>
              </div>
              <div className="chart-body">
                {data.requests_by_category.length === 0 ? (
                  <div style={{ fontSize: "0.82rem", color: "var(--text-muted)", padding: "2rem 0" }}>
                    No category data in this period.
                  </div>
                ) : (
                  <PlotlyChart
                    height={260}
                    data={[
                      {
                        x: data.requests_by_category.map((c) => c.category),
                        y: data.requests_by_category.map((c) => c.count),
                        type: "bar",
                        marker: { color: "#0f766e" },
                      } as Data,
                    ]}
                  />
                )}
              </div>
            </div>

            <div className="panel" style={{ marginBottom: 0 }}>
              <div className="panel-head">
                <span className="panel-title">Top Datasets by Access Grants</span>
              </div>
              <div className="panel-body">
                {data.top_datasets_by_grants.length === 0 ? (
                  <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No grants yet.</div>
                ) : (
                  data.top_datasets_by_grants.map((d, i) => (
                    <div key={d.title} className="act-item">
                      <div className="act-dot">{i + 1}</div>
                      <div style={{ display: "flex", justifyContent: "space-between", flex: 1 }}>
                        <span className="act-text">{d.title}</span>
                        <span style={{ fontWeight: 700, color: "var(--accent)", fontSize: "0.82rem" }}>
                          {d.count}
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}
