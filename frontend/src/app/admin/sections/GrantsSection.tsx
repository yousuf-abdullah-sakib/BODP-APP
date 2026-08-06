"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { extendGrant, getAdminGrants, revokeGrant } from "@/lib/api/admin-requests";
import GrantDurationModal from "./GrantDurationModal";
import type { GrantDetail, GrantDuration } from "@/lib/types/requests";

function daysUntil(dateStr: string) {
  const diff = new Date(dateStr).getTime() - Date.now();
  return Math.ceil(diff / 86400000);
}

export default function GrantsSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [grants, setGrants] = useState<GrantDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"" | "active" | "expiring" | "revoked">("");
  const [extendTarget, setExtendTarget] = useState<string | null>(null);

  function refetch() {
    setLoading(true);
    getAdminGrants()
      .then(setGrants)
      .catch(() => setError("Failed to load grants."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
  }, []);

  const filtered = grants.filter((g) => {
    if (search && !`${g.user.full_name} ${g.dataset.title}`.toLowerCase().includes(search.toLowerCase())) return false;
    if (statusFilter === "active") return g.status === "active";
    if (statusFilter === "expiring") return g.status === "active" && daysUntil(g.expires_at) <= 30;
    if (statusFilter === "revoked") return g.status === "revoked";
    return true;
  });

  async function handleRevoke(id: string, userName: string, datasetTitle: string) {
    const ok = await confirm({
      title: "Revoke Access",
      message: (
        <>
          Revoke <b>{userName}</b>&apos;s access to <b>&ldquo;{datasetTitle}&rdquo;</b>?
        </>
      ),
      confirmLabel: "Revoke",
      danger: true,
    });
    if (!ok) return;
    try {
      await revokeGrant(id);
      toast("Access grant revoked.", "info");
      refetch();
    } catch {
      toast("Failed to revoke grant.", "error");
    }
  }

  async function handleExtendConfirm(duration: GrantDuration, customDate?: string) {
    if (!extendTarget) return;
    try {
      await extendGrant(extendTarget, duration, customDate);
      toast("Grant extended.", "success");
      setExtendTarget(null);
      refetch();
    } catch {
      toast("Failed to extend grant.", "error");
    }
  }

  function exportCSV() {
    const header = ["Grant ID", "User", "Dataset", "Granted By", "Granted", "Expires", "Status"];
    const rows = filtered.map((g) => [
      g.id,
      g.user.full_name,
      g.dataset.title,
      g.granted_by_name ?? "",
      g.granted_at,
      g.expires_at,
      g.status,
    ]);
    const csv = [header, ...rows].map((r) => r.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "bodp_grants.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Access Grants</div>
          <div className="dash-sub">Manage active dataset access grants and expirations.</div>
        </div>
        <button className="btn-outline" onClick={exportCSV}>
          ⬇ Export CSV
        </button>
      </div>

      <div className="filter-toolbar">
        <input type="text" placeholder="Search by user or dataset…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}>
          <option value="">All Status</option>
          <option value="active">Active</option>
          <option value="expiring">Expiring &lt; 30 days</option>
          <option value="revoked">Revoked</option>
        </select>
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading grants…</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>User</th>
                <th>Dataset</th>
                <th>Granted By</th>
                <th>Granted</th>
                <th>Expires</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((g) => {
                const expiringSoon = g.status === "active" && daysUntil(g.expires_at) <= 30;
                return (
                  <tr key={g.id}>
                    <td style={{ fontWeight: 500 }}>{g.user.full_name}</td>
                    <td style={{ fontSize: "0.82rem" }}>{g.dataset.title}</td>
                    <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>{g.granted_by_name ?? "—"}</td>
                    <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                      {new Date(g.granted_at).toLocaleDateString()}
                    </td>
                    <td
                      style={{
                        color: expiringSoon ? "var(--yellow)" : "var(--text-muted)",
                        fontSize: "0.78rem",
                        fontWeight: expiringSoon ? 700 : 400,
                      }}
                    >
                      {new Date(g.expires_at).toLocaleDateString()}
                    </td>
                    <td>
                      {g.status === "active" ? (
                        expiringSoon ? (
                          <span className="badge badge-caution">⚠ Expiring</span>
                        ) : (
                          <span className="badge badge-approved">● Active</span>
                        )
                      ) : (
                        <span className="badge badge-revoked">{g.status === "revoked" ? "Revoked" : "Expired"}</span>
                      )}
                    </td>
                    <td>
                      {g.status === "active" ? (
                        <div className="flex-gap">
                          <button className="btn-icon-sm" title="Extend access" onClick={() => setExtendTarget(g.id)}>
                            📅
                          </button>
                          <button
                            className="btn-icon-sm danger"
                            title="Revoke access"
                            onClick={() => handleRevoke(g.id, g.user.full_name, g.dataset.title)}
                          >
                            🔒
                          </button>
                        </div>
                      ) : (
                        <span style={{ color: "var(--text-muted)", fontSize: "0.72rem" }}>—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {extendTarget && (
        <GrantDurationModal
          title="Extend Access Duration"
          onClose={() => setExtendTarget(null)}
          onConfirm={handleExtendConfirm}
        />
      )}
    </>
  );
}
