"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import {
  getAdminTeam,
  reactivateAdmin,
  removeAdmin,
  resendAdminInvite,
  suspendAdmin,
} from "@/lib/api/admin-team";
import AdminTeamModal from "./AdminTeamModal";
import type { AdminTeamMemberPublic } from "@/lib/types/admin-team";

export default function AdminManagementSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [team, setTeam] = useState<AdminTeamMemberPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminTeamMemberPublic | null | "new">(null);
  const [busyId, setBusyId] = useState<string | null>(null);

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

  async function handleSuspend(m: AdminTeamMemberPublic) {
    const ok = await confirm({
      title: "Suspend Admin",
      message: (
        <>
          Suspend <b>{m.full_name}</b>? They will lose administrator access immediately, but their
          account and roles stay intact — you can reactivate them later.
        </>
      ),
      confirmLabel: "Suspend",
      danger: true,
    });
    if (!ok) return;
    setBusyId(m.id);
    try {
      await suspendAdmin(m.id);
      toast(`${m.full_name} suspended.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to suspend admin.", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReactivate(m: AdminTeamMemberPublic) {
    setBusyId(m.id);
    try {
      await reactivateAdmin(m.id);
      toast(`${m.full_name} reactivated.`, "success");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to reactivate admin.", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleResendInvite(m: AdminTeamMemberPublic) {
    setBusyId(m.id);
    try {
      const { email_sent } = await resendAdminInvite(m.id);
      toast(
        email_sent ? `Invite email resent to ${m.email}.` : "Failed to send the invite email.",
        email_sent ? "success" : "error"
      );
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to resend invite.", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleRemove(m: AdminTeamMemberPublic) {
    const ok = await confirm({
      title: "Permanently Delete Admin",
      message: (
        <>
          Permanently delete <b>{m.full_name}</b>&apos;s account? This cannot be undone — their
          personal information (including email) will be erased and <b>{m.email}</b> will become
          available for a new registration or invite. If you only want to revoke access for now,
          use Suspend instead.
        </>
      ),
      confirmLabel: "Delete Permanently",
      danger: true,
    });
    if (!ok) return;
    setBusyId(m.id);
    try {
      await removeAdmin(m.id);
      toast(`${m.full_name} permanently deleted.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to delete admin.", "error");
    } finally {
      setBusyId(null);
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
                      <button
                        className="btn-icon-sm"
                        title="Edit"
                        disabled={busyId === m.id}
                        onClick={() => setEditing(m)}
                      >
                        ✎
                      </button>
                      {!m.last_active_at && (
                        <button
                          className="btn-icon-sm"
                          title="Resend invite email"
                          disabled={busyId === m.id}
                          onClick={() => handleResendInvite(m)}
                        >
                          ✉
                        </button>
                      )}
                      {m.status === "suspended" ? (
                        <button
                          className="btn-icon-sm"
                          title="Reactivate"
                          disabled={busyId === m.id}
                          onClick={() => handleReactivate(m)}
                        >
                          ↻
                        </button>
                      ) : (
                        <button
                          className="btn-icon-sm"
                          title="Suspend (reversible)"
                          disabled={busyId === m.id}
                          onClick={() => handleSuspend(m)}
                        >
                          ⏸
                        </button>
                      )}
                      <button
                        className="btn-icon-sm danger"
                        title="Permanently delete"
                        disabled={busyId === m.id}
                        onClick={() => handleRemove(m)}
                      >
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
