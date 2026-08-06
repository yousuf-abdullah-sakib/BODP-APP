"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/context/SessionContext";
import { getOverview } from "@/lib/api/me";
import type { ActivityItem, OverviewResponse } from "@/lib/types/me";

const ACTIVITY_ICONS: Record<string, string> = {
  request_submitted: "📨",
  request_approved: "✅",
  request_rejected: "🚫",
  download: "⬇️",
  approve: "✅",
  reject: "🚫",
  revoke: "⚠️",
  user: "👤",
  dataset: "🗂️",
  content: "📝",
  login: "🔑",
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diffMs = Date.now() - then;
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin} minute${diffMin === 1 ? "" : "s"} ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hour${diffHr === 1 ? "" : "s"} ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30) return `${diffDay} day${diffDay === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString();
}

export default function OverviewSection({ onNavigate }: { onNavigate: (key: string) => void }) {
  const router = useRouter();
  const { user } = useSession();
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getOverview()
      .then((data) => {
        if (!cancelled) setOverview(data);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load your overview.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const firstName = user?.full_name?.split(" ").slice(-1)[0] ?? "Researcher";
  const memberSince = overview
    ? new Date(overview.member_since).toLocaleDateString(undefined, { month: "short", year: "numeric" })
    : "";

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Good day, {firstName}! 👋</div>
          <div className="dash-sub">Here&apos;s what&apos;s happening with your research activities.</div>
        </div>
        <div style={{ display: "flex", gap: "0.6rem" }}>
          <button className="btn-outline" onClick={() => router.push("/catalog")}>
            Browse Datasets
          </button>
          <button className="btn-primary" onClick={() => router.push("/catalog")}>
            New Request
          </button>
        </div>
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading || !overview ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading your overview…</p>
        </div>
      ) : (
        <>
          <div className="stat-row">
            <div className="stat-card blue">
              <div className="stat-card-head">
                <span className="stat-card-label">Approved Datasets</span>
                <span className="stat-card-icon">✅</span>
              </div>
              <div className="stat-card-num">{overview.approved_requests}</div>
            </div>
            <div className="stat-card orange">
              <div className="stat-card-head">
                <span className="stat-card-label">Pending Requests</span>
                <span className="stat-card-icon">⏳</span>
              </div>
              <div className="stat-card-num">{overview.pending_requests}</div>
              <div className="stat-card-delta neutral">Awaiting review</div>
            </div>
            <div className="stat-card green">
              <div className="stat-card-head">
                <span className="stat-card-label">Total Downloads</span>
                <span className="stat-card-icon">⬇️</span>
              </div>
              <div className="stat-card-num">{overview.total_downloads}</div>
            </div>
            <div className="stat-card purple">
              <div className="stat-card-head">
                <span className="stat-card-label">Datasets Viewed</span>
                <span className="stat-card-icon">👁️</span>
              </div>
              <div className="stat-card-num">{overview.datasets_viewed}</div>
            </div>
            <div className="stat-card cyan">
              <div className="stat-card-head">
                <span className="stat-card-label">Member Since</span>
                <span className="stat-card-icon">🏅</span>
              </div>
              <div className="stat-card-num" style={{ fontSize: "1.2rem" }}>
                {memberSince}
              </div>
            </div>
            <div className="stat-card red">
              <div className="stat-card-head">
                <span className="stat-card-label">Data Extracted</span>
                <span className="stat-card-icon">📦</span>
              </div>
              <div className="stat-card-num" style={{ fontSize: "1.2rem" }}>
                {formatBytes(overview.total_extracted_bytes)}
              </div>
            </div>
          </div>

          <div className="panel-grid-2">
            <div className="panel">
              <div className="panel-head">
                <span className="panel-title">My Requests</span>
                <button className="panel-link" onClick={() => onNavigate("requests")}>
                  View all →
                </button>
              </div>
              <div className="panel-body">
                <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", lineHeight: 1.8 }}>
                  {overview.pending_requests} pending, {overview.approved_requests} approved,{" "}
                  {overview.datasets_granted} dataset{overview.datasets_granted === 1 ? "" : "s"} currently
                  granted.
                </div>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <span className="panel-title">Recent Activity</span>
              </div>
              <div className="panel-body">
                {overview.recent_activity.length === 0 ? (
                  <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>
                    No activity yet — browse the catalog to get started.
                  </div>
                ) : (
                  <div className="activity-list">
                    {overview.recent_activity.map((item: ActivityItem, i: number) => (
                      <div className="act-item" key={i}>
                        <div className="act-dot">{ACTIVITY_ICONS[item.type] ?? "•"}</div>
                        <div>
                          <div className="act-text">{item.description}</div>
                          <div className="act-time">{relativeTime(item.occurred_at)}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="panel" style={{ marginBottom: "1.6rem" }}>
            <div className="panel-head">
              <span className="panel-title">Quick Actions</span>
            </div>
            <div className="panel-body">
              <div className="qa-grid">
                <div className="qa-card" onClick={() => router.push("/catalog")}>
                  <div className="qa-icon">🔍</div>
                  <div className="qa-title">Browse Data</div>
                  <div className="qa-desc">Search and explore datasets</div>
                </div>
                <div className="qa-card" onClick={() => router.push("/catalog")}>
                  <div className="qa-icon">📤</div>
                  <div className="qa-title">Request Data</div>
                  <div className="qa-desc">Request restricted datasets</div>
                </div>
                <div className="qa-card" onClick={() => onNavigate("datasets")}>
                  <div className="qa-icon">🗄️</div>
                  <div className="qa-title">My Datasets</div>
                  <div className="qa-desc">View approved and saved datasets</div>
                </div>
                <div className="qa-card" onClick={() => onNavigate("notifications")}>
                  <div className="qa-icon">🔔</div>
                  <div className="qa-title">Notifications</div>
                  <div className="qa-desc">Check updates on your requests</div>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}
