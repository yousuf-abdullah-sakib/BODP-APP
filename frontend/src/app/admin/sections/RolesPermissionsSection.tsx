"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { deleteRole, getRoles } from "@/lib/api/admin-roles";
import RoleModal from "./RoleModal";
import type { RolePublic } from "@/lib/types/admin-roles";

// Mirrors backend/app/services/admin_roles_service.py's _SYSTEM_ROLE_NAMES —
// these can't be deleted or renamed; their permissions (except
// Administrator's, which are always full) stay editable here.
const SYSTEM_ROLES = new Set(["Administrator", "User", "Data Manager", "Reviewer", "Content Editor"]);

export default function RolesPermissionsSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [roles, setRoles] = useState<RolePublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<RolePublic | null | "new">(null);

  function refetch() {
    setLoading(true);
    getRoles()
      .then(setRoles)
      .catch(() => setError("Failed to load roles."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
  }, []);

  async function handleDelete(role: RolePublic) {
    const ok = await confirm({
      title: "Delete Role",
      message:
        role.user_count > 0 ? (
          <>
            Delete <b>{role.name}</b>? This role is currently assigned to <b>{role.user_count}</b> user(s) — they
            will lose these permissions.
          </>
        ) : (
          <>
            Delete <b>{role.name}</b>? This cannot be undone.
          </>
        ),
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteRole(role.id);
      toast(`${role.name} deleted.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to delete role.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Roles &amp; Permissions</div>
          <div className="dash-sub">Define roles and control what each can access.</div>
        </div>
        <button className="btn-primary" onClick={() => setEditing("new")}>
          + New Role
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
          <p>Loading roles…</p>
        </div>
      ) : (
        <div className="dl-grid">
          {roles.map((role) => {
            const isSystemRole = SYSTEM_ROLES.has(role.name);
            return (
              <div className="panel" key={role.id}>
                <div className="panel-head">
                  <span className="panel-title">{role.name}</span>
                  <span className="chip">{role.user_count} user(s)</span>
                </div>
                <div className="panel-body">
                  <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "0.9rem", lineHeight: 1.6 }}>
                    {role.description}
                  </p>
                  {role.permissions.length === 0 ? (
                    <div style={{ fontSize: "0.76rem", color: "var(--text-muted)" }}>No elevated permissions.</div>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "1rem" }}>
                      {role.permissions.map((p) => (
                        <span key={p} className="chip">
                          {p}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="flex-gap">
                    <button className="btn-icon-sm" title="Edit" onClick={() => setEditing(role)}>
                      ✎
                    </button>
                    <button
                      className="btn-icon-sm danger"
                      title={isSystemRole ? "Cannot delete a system role" : "Delete"}
                      disabled={isSystemRole}
                      style={isSystemRole ? { opacity: 0.4, cursor: "not-allowed" } : undefined}
                      onClick={() => !isSystemRole && handleDelete(role)}
                    >
                      🗑
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {editing !== null && (
        <RoleModal role={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refetch} />
      )}
    </>
  );
}
