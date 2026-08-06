"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import StatusBadge from "@/components/ui/StatusBadge";
import { approveRequest, getAdminRequests, rejectRequest } from "@/lib/api/admin-requests";
import ModifyApproveModal from "./ModifyApproveModal";
import RejectRequestModal from "./RejectRequestModal";
import GrantDurationModal from "./GrantDurationModal";
import type { GrantDuration, RequestDetail, SearchCriteria } from "@/lib/types/requests";

const TABS: { key: "all" | RequestDetail["status"]; label: string }[] = [
  { key: "all", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
];

function initialsOf(name: string) {
  return name
    .replace(/^Dr\.\s*/, "")
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export default function AdminRequestsSection({ onMutate }: { onMutate?: () => void }) {
  const { toast } = useToast();
  const [tab, setTab] = useState<"all" | RequestDetail["status"]>("pending");
  const [requests, setRequests] = useState<RequestDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [modifyTarget, setModifyTarget] = useState<RequestDetail | null>(null);
  const [rejectTarget, setRejectTarget] = useState<RequestDetail | null>(null);
  const [durationTarget, setDurationTarget] = useState<{
    request: RequestDetail;
    note: string;
    criteria?: SearchCriteria;
  } | null>(null);

  function refetch() {
    setLoading(true);
    getAdminRequests()
      .then(setRequests)
      .catch(() => setError("Failed to load requests."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = tab === "all" ? requests : requests.filter((r) => r.status === tab);

  function handleApproveAll(r: RequestDetail) {
    setDurationTarget({ request: r, note: "" });
  }

  function handleModifyConfirm(note: string, criteria: SearchCriteria | undefined) {
    if (!modifyTarget) return;
    setDurationTarget({ request: modifyTarget, note, criteria });
    setModifyTarget(null);
  }

  async function handleDurationConfirm(duration: GrantDuration, customDate?: string) {
    if (!durationTarget) return;
    const { request: r, note, criteria } = durationTarget;
    try {
      await approveRequest(r.id, { duration, customExpiresAt: customDate, note, searchCriteria: criteria });
      toast(`Approved. ${r.user.full_name} now has access to "${r.dataset.title}".`, "success");
      setDurationTarget(null);
      refetch();
      onMutate?.();
    } catch {
      toast("Failed to approve request.", "error");
    }
  }

  async function handleRejectConfirm(reason: string) {
    if (!rejectTarget) return;
    try {
      await rejectRequest(rejectTarget.id, reason);
      toast(`Request rejected.`, "info");
      setRejectTarget(null);
      refetch();
      onMutate?.();
    } catch {
      toast("Failed to reject request.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Dataset Requests</div>
          <div className="dash-sub">Review and approve researcher access requests.</div>
        </div>
      </div>

      <div className="filter-tab-row">
        {TABS.map((t) => (
          <button key={t.key} className={`filter-tab-btn${tab === t.key ? " active" : ""}`} onClick={() => setTab(t.key)}>
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
          <p>Loading requests…</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {filtered.length === 0 && (
            <div className="empty-state">
              <div className="es-icon">📭</div>
              <p>No requests in this category.</p>
            </div>
          )}
          {filtered.map((r) => (
            <div className="panel" key={r.id}>
              <div className="panel-head">
                <div style={{ display: "flex", alignItems: "center", gap: "0.7rem" }}>
                  <div className="req-card-avatar">{initialsOf(r.user.full_name)}</div>
                  <div>
                    <div className="panel-title">{r.user.full_name}</div>
                    <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>
                      {r.user.institution ?? r.user.email}
                    </div>
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <StatusBadge status={r.status} />
                  <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                    Submitted {new Date(r.submitted_at).toLocaleDateString()}
                  </div>
                </div>
              </div>
              <div className="panel-body">
                <div className="mini-label">Requested Dataset</div>
                <div className="req-dataset-list" style={{ marginBottom: "1rem" }}>
                  <div className="req-dataset-row">
                    <span>📦 {r.dataset.title}</span>
                    <span className="chip">{r.dataset.code}</span>
                  </div>
                </div>

                <div className="mini-label">Research Justification</div>
                <div className="req-letter-box" style={{ marginBottom: "1rem" }}>{r.justification}</div>

                {r.admin_note && (
                  <div className="req-letter-box note-danger" style={{ marginTop: "0.9rem" }}>
                    <b style={{ color: "var(--red)" }}>Admin note:</b> {r.admin_note}
                  </div>
                )}

                {r.status === "pending" ? (
                  <div className="req-actions-row">
                    <button className="btn-ghost-sm" onClick={() => setModifyTarget(r)}>
                      ✎ Modify &amp; Approve
                    </button>
                    <button className="btn-danger-sm" onClick={() => setRejectTarget(r)}>
                      ✗ Reject
                    </button>
                    <button className="btn-success-sm" onClick={() => handleApproveAll(r)}>
                      ✓ Approve
                    </button>
                  </div>
                ) : (
                  <div style={{ fontSize: "0.74rem", color: "var(--text-muted)", marginTop: "0.9rem" }}>
                    {r.reviewed_at
                      ? `Last updated: ${new Date(r.reviewed_at).toLocaleDateString()}`
                      : null}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {modifyTarget && (
        <ModifyApproveModal request={modifyTarget} onClose={() => setModifyTarget(null)} onConfirm={handleModifyConfirm} />
      )}
      {rejectTarget && <RejectRequestModal onClose={() => setRejectTarget(null)} onConfirm={handleRejectConfirm} />}
      {durationTarget && (
        <GrantDurationModal
          title="Set Access Duration"
          onClose={() => setDurationTarget(null)}
          onConfirm={handleDurationConfirm}
        />
      )}
    </>
  );
}
