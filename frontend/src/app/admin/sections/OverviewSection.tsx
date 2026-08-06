"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getAdminOverview, getStorageCapacity, updateStorageCapacity } from "@/lib/api/admin-overview";
import type { AdminOverviewResponse, StatCardValue } from "@/lib/types/admin-overview";

const CATEGORY_COLORS = ["#0891b2", "#f97316", "#14b8a6", "#22c55e", "#a855f7", "#f59e0b", "#0f766e", "#dc2626"];

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(1)} ${units[i]}`;
}

function Sparkline({ data, color }: { data: number[]; color: string }) {
  const width = 100;
  const height = 28;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - min) / range) * height;
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg className="stat-card-sparkline" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline points={points} fill="none" stroke={color} strokeWidth="2" />
    </svg>
  );
}

function StatCard({
  label,
  icon,
  stat,
  accentClass,
  color,
}: {
  label: string;
  icon: string;
  stat: StatCardValue;
  accentClass: string;
  color: string;
}) {
  return (
    <div className={`stat-card ${accentClass}`}>
      <div className="stat-card-head">
        <span className="stat-card-label">{label}</span>
        <span className="stat-card-icon">{icon}</span>
      </div>
      <div className="stat-card-num">{stat.current.toLocaleString()}</div>
      {stat.delta_pct !== null && (
        <div className={`stat-card-delta ${stat.delta_pct >= 0 ? "up" : "down"}`}>
          {stat.delta_pct >= 0 ? "▲" : "▼"} {Math.abs(stat.delta_pct).toFixed(1)}% vs last period
        </div>
      )}
      {stat.sparkline.length >= 2 && <Sparkline data={stat.sparkline} color={color} />}
    </div>
  );
}

export default function OverviewSection({ onNavigate }: { onNavigate: (key: string) => void }) {
  const { toast } = useToast();
  const [data, setData] = useState<AdminOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingCapacity, setEditingCapacity] = useState(false);
  const [capacityInput, setCapacityInput] = useState("");
  const [savingCapacity, setSavingCapacity] = useState(false);

  function refetch() {
    setLoading(true);
    getAdminOverview()
      .then(setData)
      .catch(() => setError("Failed to load overview."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
  }, []);

  async function handleSaveCapacity() {
    const bytes = capacityInput.trim() === "" ? null : Number(capacityInput);
    if (bytes !== null && (Number.isNaN(bytes) || bytes <= 0)) {
      toast("Enter a valid capacity in bytes.", "error");
      return;
    }
    setSavingCapacity(true);
    try {
      await updateStorageCapacity(bytes);
      toast("Storage capacity updated.", "success");
      setEditingCapacity(false);
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to update storage capacity.", "error");
    } finally {
      setSavingCapacity(false);
    }
  }

  useEffect(() => {
    if (editingCapacity) {
      getStorageCapacity()
        .then((s) => setCapacityInput(s.storage_capacity_bytes?.toString() ?? ""))
        .catch(() => setCapacityInput(""));
    }
  }, [editingCapacity]);

  if (error) {
    return (
      <>
        <div className="dash-header">
          <div>
            <div className="dash-title">Admin Overview</div>
          </div>
        </div>
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      </>
    );
  }

  if (loading || !data) {
    return (
      <>
        <div className="dash-header">
          <div>
            <div className="dash-title">Admin Overview</div>
          </div>
        </div>
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading overview…</p>
        </div>
      </>
    );
  }

  const categoryEntries = Object.entries(data.category_distribution);
  const categoryTotal = categoryEntries.reduce((a, [, c]) => a + c, 0) || 1;
  const categoryMax = Math.max(...categoryEntries.map(([, c]) => c), 1);

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Admin Overview</div>
          <div className="dash-sub">Complete system management, data control, and analytics.</div>
        </div>
      </div>

      <div className="stat-row">
        <StatCard label="Total Users" icon="👥" stat={data.total_users} accentClass="blue" color="var(--stat-blue)" />
        <StatCard label="Active Users" icon="🟢" stat={data.active_users} accentClass="green" color="var(--stat-green)" />
        <StatCard label="Total Datasets" icon="🗄️" stat={data.total_datasets} accentClass="purple" color="var(--stat-purple)" />
        <StatCard label="Downloads" icon="⬇️" stat={data.downloads} accentClass="orange" color="var(--stat-orange)" />
        <StatCard label="Pending Requests" icon="⏳" stat={data.pending_requests} accentClass="red" color="var(--stat-red)" />
      </div>

      <div className="panel-grid-2">
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Recent Data Requests</span>
            <button className="panel-link" onClick={() => onNavigate("requests")}>
              View All Requests →
            </button>
          </div>
          <div className="panel-body">
            {data.recent_requests.length === 0 && (
              <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No pending requests.</div>
            )}
            <div className="activity-list">
              {data.recent_requests.map((r) => (
                <div key={r.id} className="act-item">
                  <div className="act-dot">📨</div>
                  <div>
                    <div className="act-text">
                      <b>{r.requester_name}</b> requested {r.dataset_title}
                    </div>
                    <div className="act-time">{new Date(r.submitted_at).toLocaleString()}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Recent Admin Activity</span>
          </div>
          <div className="panel-body">
            {data.recent_activity.length === 0 && (
              <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No recent activity.</div>
            )}
            <div className="activity-list">
              {data.recent_activity.map((a, i) => (
                <div className="act-item" key={i}>
                  <div className="act-dot">📝</div>
                  <div>
                    <div className="act-text">{a.description}</div>
                    <div className="act-time">{new Date(a.occurred_at).toLocaleString()}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="panel-grid-2">
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Dataset Category Distribution</span>
          </div>
          <div className="panel-body">
            {categoryEntries.length === 0 ? (
              <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No datasets categorized yet.</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
                {categoryEntries.map(([cat, count], i) => (
                  <div key={cat}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", marginBottom: "0.25rem" }}>
                      <span style={{ color: "var(--text-secondary)" }}>{cat}</span>
                      <span style={{ color: "var(--text-muted)" }}>
                        {count} ({Math.round((count / categoryTotal) * 100)}%)
                      </span>
                    </div>
                    <div style={{ background: "rgba(15,118,110,0.08)", borderRadius: 100, height: 8, overflow: "hidden" }}>
                      <div
                        style={{
                          width: `${(count / categoryMax) * 100}%`,
                          height: "100%",
                          background: CATEGORY_COLORS[i % CATEGORY_COLORS.length],
                          borderRadius: 100,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Storage Usage</span>
          </div>
          <div className="panel-body">
            <div style={{ fontSize: "1rem", fontWeight: 800, color: "var(--text-primary)" }}>
              {formatBytes(data.storage.used_bytes)} Used
            </div>
            {data.storage.capacity_bytes !== null ? (
              <>
                <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", margin: "0.3rem 0 0.6rem" }}>
                  {formatBytes(data.storage.used_bytes)} of {formatBytes(data.storage.capacity_bytes)} used
                  {data.storage.percent_used !== null && ` (${data.storage.percent_used.toFixed(1)}%)`}
                </div>
                <div className="progress-bar-wrap">
                  <div
                    className="progress-bar-fill"
                    style={{ width: `${Math.min(data.storage.percent_used ?? 0, 100)}%` }}
                  />
                </div>
                <button
                  className="btn-ghost-sm"
                  style={{ marginTop: "0.8rem" }}
                  onClick={() => setEditingCapacity((v) => !v)}
                >
                  Configure storage capacity
                </button>
              </>
            ) : (
              <>
                <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", margin: "0.3rem 0 0.6rem" }}>
                  No storage capacity configured.
                </div>
                <button className="btn-ghost-sm" onClick={() => setEditingCapacity((v) => !v)}>
                  Configure storage capacity
                </button>
              </>
            )}
            {editingCapacity && (
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.8rem" }}>
                <input
                  type="number"
                  className="form-input"
                  placeholder="Bytes"
                  value={capacityInput}
                  onChange={(e) => setCapacityInput(e.target.value)}
                  style={{ maxWidth: 180 }}
                />
                <button className="btn-primary-sm" onClick={handleSaveCapacity} disabled={savingCapacity}>
                  {savingCapacity ? "Saving…" : "Save"}
                </button>
                <button className="btn-cancel" onClick={() => setEditingCapacity(false)}>
                  Cancel
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="panel-grid-2">
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Top Downloaded Datasets</span>
          </div>
          <div className="panel-body">
            {data.top_downloaded.length === 0 ? (
              <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No downloads recorded yet.</div>
            ) : (
              data.top_downloaded.map((d, i) => (
                <div key={d.dataset_title} className="act-item">
                  <div className="act-dot">{i + 1}</div>
                  <div style={{ display: "flex", justifyContent: "space-between", flex: 1 }}>
                    <span className="act-text">{d.dataset_title}</span>
                    <span style={{ fontWeight: 700, color: "var(--accent)", fontSize: "0.82rem" }}>
                      {d.count.toLocaleString()}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">System Health</span>
          </div>
          <div className="panel-body">
            <div className="health-row">
              <span>
                <span className={`health-dot ${data.system_health.database ? "healthy" : "bad"}`} />
                Database
              </span>
              <span style={{ color: data.system_health.database ? "var(--green)" : "var(--red)" }}>
                {data.system_health.database ? "Healthy" : "Unavailable"}
              </span>
            </div>
            <div className="health-row">
              <span>
                <span className={`health-dot ${data.system_health.redis ? "healthy" : "bad"}`} />
                Redis
              </span>
              <span style={{ color: data.system_health.redis ? "var(--green)" : "var(--red)" }}>
                {data.system_health.redis ? "Healthy" : "Unavailable"}
              </span>
            </div>
            <div className="health-row">
              <span>
                <span className={`health-dot ${data.system_health.storage ? "healthy" : "bad"}`} />
                Storage
              </span>
              <span style={{ color: data.system_health.storage ? "var(--green)" : "var(--red)" }}>
                {data.system_health.storage ? "Healthy" : "Unavailable"}
              </span>
            </div>
            <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.6rem" }}>
              Checked {new Date(data.system_health.checked_at).toLocaleString()}
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Quick Actions</span>
        </div>
        <div className="panel-body">
          <div className="qa-grid">
            <div className="qa-card" onClick={() => onNavigate("datasets")}>
              <div className="qa-icon">➕</div>
              <div className="qa-title">Add Dataset</div>
            </div>
            <div className="qa-card" onClick={() => onNavigate("users")}>
              <div className="qa-icon">👥</div>
              <div className="qa-title">Manage Users</div>
            </div>
            <div className="qa-card" onClick={() => onNavigate("requests")}>
              <div className="qa-icon">📋</div>
              <div className="qa-title">Review Requests</div>
              {data.pending_requests.current > 0 && (
                <span className="dni-badge" style={{ background: "var(--red)", color: "#fff" }}>
                  {data.pending_requests.current}
                </span>
              )}
            </div>
            <div className="qa-card" onClick={() => onNavigate("grants")}>
              <div className="qa-icon">🔑</div>
              <div className="qa-title">Access Grants</div>
            </div>
            <div className="qa-card" onClick={() => onNavigate("quality")}>
              <div className="qa-icon">🧪</div>
              <div className="qa-title">Data Quality Check</div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
