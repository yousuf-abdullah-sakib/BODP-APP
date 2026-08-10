"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getAdminTeam, removeAdmin } from "@/lib/api/admin-team";
import AdminTeamModal from "./AdminTeamModal";
import type { AdminTeamMemberPublic } from "@/lib/types/admin-team";

export default function AdminManagementSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [team, setTeam] = useState<AdminTeamMemberPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminTeamMemberPublic | null | "new">(null);

  function refetch() {
    setLoading(true);
    getAdminTeam()
      .then(setTeam)
      .catch(() => setError("Failed to load admin team."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
  }, []);

  async function handleRemove(m: AdminTeamMemberPublic) {
    const ok = await confirm({
      title: "Remove Admin",
      message: (
        <>
          Suspend <b>{m.full_name}</b>? They will lose administrator access immediately.
        </>
      ),
      confirmLabel: "Remove",
      danger: true,
    });
    if (!ok) return;
    try {
      await removeAdmin(m.id);
      toast(`${m.full_name} removed from admin team.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to remove admin.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Admin Management</div>
          <div className="dash-sub">Manage administrators and their roles separately from regular users.</div>
        </div>
        <button className="btn-primary" onClick={() => setEditing("new")}>
          + Invite Admin
        </button>
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading admin team…</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Roles</th>
                <th>Last Active</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {team.map((m) => (
                <tr key={m.id}>
                  <td style={{ fontWeight: 600 }}>{m.full_name}</td>
                  <td style={{ fontSize: "0.82rem" }}>{m.email}</td>
                  <td>
                    <div style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
                      {m.roles.length > 0 ? (
                        m.roles.map((r) => (
                          <span className="chip" key={r}>
                            {r}
                          </span>
                        ))
                      ) : (
                        <span className="chip">—</span>
                      )}
                    </div>
                  </td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                    {m.last_active_at ? new Date(m.last_active_at).toLocaleString() : "Never"}
                  </td>
                  <td>
                    <span className={`badge badge-${m.status === "active" ? "approved" : "suspended"}`}>{m.status}</span>
                  </td>
                  <td>
                    <div className="flex-gap">
                      <button className="btn-icon-sm" title="Edit" onClick={() => setEditing(m)}>
                        ✎
                      </button>
                      <button className="btn-icon-sm danger" title="Remove" onClick={() => handleRemove(m)}>
                        🗑
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing !== null && (
        <AdminTeamModal member={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refetch} />
      )}
    </>
  );
}
