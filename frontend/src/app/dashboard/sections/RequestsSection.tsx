"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import CategoryPill from "@/components/ui/CategoryPill";
import StatusBadge from "@/components/ui/StatusBadge";
import { getMyRequests } from "@/lib/api/requests";
import type { RequestSummary } from "@/lib/types/requests";

const STEPS = ["Submitted", "Under Review", "Decision", "Complete"];

function stepIndex(status: RequestSummary["status"]): number {
  if (status === "pending") return 1;
  if (status === "approved") return 3;
  return 2;
}

const TABS: { key: "all" | RequestSummary["status"]; label: string }[] = [
  { key: "all", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
];

export default function RequestsSection() {
  const router = useRouter();
  const [tab, setTab] = useState<"all" | RequestSummary["status"]>("all");
  const [requests, setRequests] = useState<RequestSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMyRequests()
      .then((data) => {
        if (!cancelled) setRequests(data);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load your requests.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = tab === "all" ? requests : requests.filter((r) => r.status === tab);

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">My Requests</div>
          <div className="dash-sub">Track the status of your dataset access requests.</div>
        </div>
        <button className="btn-primary" onClick={() => router.push("/catalog")}>
          + New Request
        </button>
      </div>

      <div className="filter-tab-row">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`filter-tab-btn${tab === t.key ? " active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label} ({t.key === "all" ? requests.length : requests.filter((r) => r.status === t.key).length})
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
          <p>Loading your requests…</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">📭</div>
          <p>No requests in this category.</p>
          <button className="btn-primary" style={{ marginTop: "1rem" }} onClick={() => router.push("/catalog")}>
            Request a dataset
          </button>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {filtered.map((r) => {
            const idx = stepIndex(r.status);
            return (
              <div className="panel" key={r.id}>
                <div className="panel-head">
                  <div>
                    <span className="panel-title">{r.dataset.title}</span>{" "}
                    <CategoryPill category={r.dataset.category} />
                  </div>
                  <StatusBadge status={r.status} />
                </div>
                <div className="panel-body">
                  <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
                    Submitted {new Date(r.submitted_at).toLocaleDateString()}
                  </div>
                  {r.status !== "rejected" ? (
                    <div className="req-stepper">
                      {STEPS.map((s, i) => (
                        <div
                          key={s}
                          className={`req-step${i < idx ? " done" : i === idx ? " current" : ""}`}
                        >
                          <div className="req-step-dot">{i < idx ? "✓" : i + 1}</div>
                          <div className="req-step-label">{s}</div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <>
                      <div className="form-alert form-alert-error">
                        <b>Admin note:</b> {r.admin_note}
                      </div>
                      <button className="btn-ghost-sm" style={{ marginTop: "0.6rem" }} onClick={() => router.push("/catalog")}>
                        Re-apply
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
