"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import {
  activateUser,
  cancelDeletionRequest,
  deleteUser,
  exportUsersCsv,
  getAdminUser,
  getAdminUsers,
  getPendingDeletionRequests,
  suspendUser,
} from "@/lib/api/admin-users";
import UserDetailModal from "./UserDetailModal";
import UserModal from "./UserModal";
import type { AdminUserDetail, AdminUserSummary } from "@/lib/types/admin-users";

export default function UsersSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [view, setView] = useState<"all" | "deletion-requests">("all");
  const [users, setUsers] = useState<AdminUserSummary[]>([]);
  const [deletionRequests, setDeletionRequests] = useState<AdminUserSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<AdminUserSummary | null | "new">(null);
  const [viewing, setViewing] = useState<AdminUserDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  function refetch() {
    setLoading(true);
    getAdminUsers({ search: search || undefined })
      .then(setUsers)
      .catch(() => setError("Failed to load users."))
      .finally(() => setLoading(false));
  }

  function refetchDeletionRequests() {
    getPendingDeletionRequests()
      .then(setDeletionRequests)
      .catch(() => {});
  }

  useEffect(() => {
    refetch();
    refetchDeletionRequests();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  async function handleCancelDeletionRequest(u: AdminUserSummary) {
    setBusyId(u.id);
    try {
      await cancelDeletionRequest(u.id);
      toast(`Deletion request cancelled for ${u.full_name}.`, "success");
      refetchDeletionRequests();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to cancel deletion request.", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(u: AdminUserSummary) {
    const ok = await confirm({
      title: "Permanently Delete Account",
      message: (
        <>
          Permanently delete <b>{u.full_name}</b>&apos;s account? This cannot be undone — their
          personal information (including email) will be erased and <b>{u.email}</b> will become
          available for a new registration. If you only want to revoke access for now, use Suspend
          instead.
        </>
      ),
      confirmLabel: "Delete Permanently",
      danger: true,
    });
    if (!ok) return;
    setBusyId(u.id);
    try {
      await deleteUser(u.id);
      toast(`${u.full_name} permanently deleted.`, "info");
      refetch();
      refetchDeletionRequests();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to delete user.", "error");
    } finally {
      setBusyId(null);
    }
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleSetStatus(u: AdminUserSummary, status: "active" | "suspended") {
    if (status === "suspended") {
      const ok = await confirm({
        title: "Suspend User",
        message: (
          <>
            Suspend <b>{u.full_name}</b>? They will be unable to sign in or access granted datasets while suspended.
          </>
        ),
        confirmLabel: "Suspend",
        danger: true,
      });
      if (!ok) return;
    }
    try {
      if (status === "suspended") await suspendUser(u.id);
      else await activateUser(u.id);
      toast(status === "suspended" ? `${u.full_name} suspended.` : `${u.full_name} activated.`, "success");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to update user status.", "error");
    }
  }

  async function handleBulkSuspend() {
    const ok = await confirm({
      title: "Suspend Users",
      message: `Suspend ${selected.size} selected user(s)?`,
      confirmLabel: "Suspend",
      danger: true,
    });
    if (!ok) return;
    await Promise.allSettled(Array.from(selected).map((id) => suspendUser(id)));
    toast(`${selected.size} user(s) suspended.`, "success");
    setSelected(new Set());
    refetch();
  }

  async function handleBulkActivate() {
    await Promise.allSettled(Array.from(selected).map((id) => activateUser(id)));
    toast(`${selected.size} user(s) activated.`, "success");
    setSelected(new Set());
    refetch();
  }

  async function handleView(u: AdminUserSummary) {
    setLoadingDetail(u.id);
    try {
      const detail = await getAdminUser(u.id);
      setViewing(detail);
    } catch {
      toast("Failed to load user details.", "error");
    } finally {
      setLoadingDetail(null);
    }
  }

  async function handleExport() {
    try {
      await exportUsersCsv();
    } catch {
      toast("Failed to export users.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Users</div>
          <div className="dash-sub">Manage researcher accounts.</div>
        </div>
        <div style={{ display: "flex", gap: "0.6rem" }}>
          <button className="btn-outline" onClick={handleExport}>
            ⬇ Export CSV
          </button>
          <button className="btn-primary" onClick={() => setEditing("new")}>
            + New User
          </button>
        </div>
      </div>

      <div className="filter-tab-row">
        <button className={`filter-tab-btn${view === "all" ? " active" : ""}`} onClick={() => setView("all")}>
          All Users ({users.length})
        </button>
        <button
          className={`filter-tab-btn${view === "deletion-requests" ? " active" : ""}`}
          onClick={() => setView("deletion-requests")}
        >
          Deletion Requests ({deletionRequests.length})
        </button>
      </div>

      {view === "all" && (
        <div className="filter-toolbar">
          <input
            type="text"
            placeholder="Search by name or email…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      )}

      {view === "all" && (
        <div className={`bulk-bar${selected.size > 0 ? " visible" : ""}`}>
          <span>{selected.size} selected</span>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="btn-outline" style={{ padding: "0.3rem 0.8rem", fontSize: "0.78rem" }} onClick={handleBulkActivate}>
              ▶ Activate
            </button>
            <button className="btn-danger-sm" onClick={handleBulkSuspend}>
              ⏸ Suspend
            </button>
            <button className="btn-cancel" onClick={() => setSelected(new Set())}>
              Clear
            </button>
          </div>
        </div>
      )}

      {view === "deletion-requests" ? (
        deletionRequests.length === 0 ? (
          <div className="empty-state">
            <div className="es-icon">🗑️</div>
            <p>No pending account deletion requests.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Requested</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {deletionRequests.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{u.full_name}</div>
                      <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>{u.email}</div>
                    </td>
                    <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                      {u.deletion_requested_at ? new Date(u.deletion_requested_at).toLocaleDateString() : "—"}
                    </td>
                    <td>
                      <span className={`badge badge-${u.status === "active" ? "approved" : "suspended"}`}>{u.status}</span>
                    </td>
                    <td>
                      <div className="flex-gap">
                        <button
                          className="btn-icon-sm"
                          title="Cancel deletion request"
                          disabled={busyId === u.id}
                          onClick={() => handleCancelDeletionRequest(u)}
                        >
                          ↺
                        </button>
                        <button
                          className="btn-icon-sm danger"
                          title="Delete now"
                          disabled={busyId === u.id}
                          onClick={() => handleDelete(u)}
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
        )
      ) : error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading users…</p>
        </div>
      ) : users.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">👥</div>
          <p>No users found.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    onChange={(e) => setSelected(e.target.checked ? new Set(users.map((u) => u.id)) : new Set())}
                  />
                </th>
                <th>Name</th>
                <th>Institution</th>
                <th>Role</th>
                <th>Datasets</th>
                <th>Joined</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className={selected.has(u.id) ? "selected" : ""}>
                  <td>
                    <input type="checkbox" checked={selected.has(u.id)} onChange={() => toggle(u.id)} />
                  </td>
                  <td>
                    <div style={{ fontWeight: 600 }}>
                      {u.full_name}
                      {u.deletion_requested_at && (
                        <span
                          className="badge badge-suspended"
                          style={{ marginLeft: "0.5rem", fontSize: "0.65rem" }}
                        >
                          deletion requested
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>{u.email}</div>
                  </td>
                  <td style={{ fontSize: "0.82rem" }}>{u.institution ?? "—"}</td>
                  <td>
                    <span className={`role-pill role-${u.role === "admin" ? "admin" : "user"}`}>{u.role}</span>
                  </td>
                  <td>{u.datasets_granted}</td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td>
                    <span className={`badge badge-${u.status === "active" ? "approved" : "suspended"}`}>{u.status}</span>
                  </td>
                  <td>
                    <div className="flex-gap">
                      <button
                        className="btn-icon-sm"
                        title="View details"
                        onClick={() => handleView(u)}
                        disabled={loadingDetail === u.id}
                      >
                        👁
                      </button>
                      <button className="btn-icon-sm" title="Edit user" onClick={() => setEditing(u)}>
                        ✎
                      </button>
                      {u.status === "active" ? (
                        <button
                          className="btn-icon-sm danger"
                          title="Suspend"
                          onClick={() => handleSetStatus(u, "suspended")}
                        >
                          ⏸
                        </button>
                      ) : (
                        <button className="btn-icon-sm" title="Activate" onClick={() => handleSetStatus(u, "active")}>
                          ▶
                        </button>
                      )}
                      <button
                        className="btn-icon-sm danger"
                        title="Permanently delete"
                        disabled={busyId === u.id}
                        onClick={() => handleDelete(u)}
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
        <UserModal user={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refetch} />
      )}
      {viewing && (
        <UserDetailModal
          user={viewing}
          onClose={() => setViewing(null)}
          onMutate={() => {
            refetch();
          }}
        />
      )}
    </>
  );
}
