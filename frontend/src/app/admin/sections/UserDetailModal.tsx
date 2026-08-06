"use client";

import { useEffect, useState } from "react";
import Modal from "@/components/ui/Modal";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getUserGrants, revokeUserGrant } from "@/lib/api/admin-users";
import type { AdminUserDetail } from "@/lib/types/admin-users";
import type { GrantSummary } from "@/lib/types/requests";

interface UserDetailModalProps {
  user: AdminUserDetail;
  onClose: () => void;
  onMutate: () => void;
}

export default function UserDetailModal({ user, onClose, onMutate }: UserDetailModalProps) {
  const confirm = useConfirm();
  const { toast } = useToast();
  const [grants, setGrants] = useState<GrantSummary[]>([]);
  const [loading, setLoading] = useState(true);

  function refetch() {
    setLoading(true);
    getUserGrants(user.id)
      .then(setGrants)
      .catch(() => setGrants([]))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user.id]);

  async function handleRevoke(grantId: string, datasetTitle: string) {
    const ok = await confirm({
      title: "Revoke Access",
      message: (
        <>
          Revoke <b>{user.full_name}</b>&apos;s access to <b>&ldquo;{datasetTitle}&rdquo;</b>?
        </>
      ),
      confirmLabel: "Revoke",
      danger: true,
    });
    if (!ok) return;
    try {
      await revokeUserGrant(user.id, grantId);
      toast("Access grant revoked.", "info");
      refetch();
      onMutate();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to revoke grant.", "error");
    }
  }

  const initials = user.full_name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <Modal title="User Details" onClose={onClose} footer={<button className="btn-ghost-sm" onClick={onClose}>Close</button>}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.9rem", marginBottom: "1.3rem" }}>
        <div className="req-card-avatar" style={{ width: 48, height: 48, fontSize: "1rem" }}>
          {initials}
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--text-primary)" }}>{user.full_name}</div>
          <div style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>{user.email}</div>
          <div style={{ display: "flex", gap: "0.4rem", marginTop: "0.4rem" }}>
            <span className={`role-pill role-${user.role === "admin" ? "admin" : "user"}`}>{user.role}</span>
            <span className={`badge badge-${user.status === "active" ? "approved" : "suspended"}`}>{user.status}</span>
          </div>
        </div>
      </div>

      <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", marginBottom: "1.3rem" }}>
        <b>Institution:</b> {user.institution ?? "—"} &nbsp;·&nbsp; <b>Joined:</b>{" "}
        {new Date(user.created_at).toLocaleDateString()}
      </div>

      <div className="mini-label">Granted Datasets ({grants.length})</div>
      <div className="req-dataset-list">
        {loading && (
          <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", padding: "0.4rem 0" }}>Loading…</div>
        )}
        {!loading && grants.length === 0 && (
          <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", padding: "0.4rem 0" }}>No datasets granted.</div>
        )}
        {grants.map((g) => (
          <div className="req-dataset-row" key={g.id}>
            <span>
              📦 {g.dataset.title}{" "}
              <span style={{ color: "var(--text-muted)", fontSize: "0.72rem" }}>
                ({g.status === "active" ? `expires ${new Date(g.expires_at).toLocaleDateString()}` : g.status})
              </span>
            </span>
            {g.status === "active" && (
              <button className="btn-icon-sm danger" title="Revoke" onClick={() => handleRevoke(g.id, g.dataset.title)}>
                🔒
              </button>
            )}
          </div>
        ))}
      </div>
    </Modal>
  );
}
