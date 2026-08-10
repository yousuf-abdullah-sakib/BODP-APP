"use client";

import { useEffect, useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { inviteAdmin } from "@/lib/api/admin-team";
import { assignRole, unassignRole } from "@/lib/api/admin-users";
import { getRoles } from "@/lib/api/admin-roles";
import type { AdminTeamMemberPublic } from "@/lib/types/admin-team";
import type { RolePublic } from "@/lib/types/admin-roles";

interface AdminTeamModalProps {
  member: AdminTeamMemberPublic | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function AdminTeamModal({ member, onClose, onSaved }: AdminTeamModalProps) {
  const { toast } = useToast();
  const isNew = !member;
  const [fullName, setFullName] = useState(member?.full_name ?? "");
  const [email, setEmail] = useState(member?.email ?? "");
  const [roles, setRoles] = useState<RolePublic[]>([]);
  const [loadingRoles, setLoadingRoles] = useState(true);
  // New invite: single role. Existing admin: a toggleable set (a user can
  // hold more than one admin-panel Role, e.g. Reviewer + Content Editor).
  const [selectedRoleId, setSelectedRoleId] = useState("");
  const [selectedRoleNames, setSelectedRoleNames] = useState<Set<string>>(
    new Set(member?.roles ?? [])
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getRoles()
      .then((all) => {
        // "User" is the plain-researcher role — Admin Management only
        // ever assigns admin-panel roles. Roles & Permissions is where
        // "User" itself would be managed, if that's ever needed.
        const adminRoles = all.filter((r) => r.name !== "User");
        setRoles(adminRoles);
        if (isNew && adminRoles.length > 0) setSelectedRoleId(adminRoles[0].id);
      })
      .catch(() => toast("Failed to load roles.", "error"))
      .finally(() => setLoadingRoles(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    if (!fullName.trim() || (isNew && !email.trim())) {
      toast("Name and email are required.", "error");
      return;
    }
    if (isNew && !selectedRoleId) {
      toast("Please select a role.", "error");
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        await inviteAdmin({ full_name: fullName.trim(), email: email.trim(), role_id: selectedRoleId });
        toast("Admin invited.", "success");
      } else {
        const before = new Set(member.roles);
        const toAdd = roles.filter((r) => selectedRoleNames.has(r.name) && !before.has(r.name));
        const toRemove = roles.filter((r) => !selectedRoleNames.has(r.name) && before.has(r.name));
        await Promise.all([
          ...toAdd.map((r) => assignRole(member.id, r.id)),
          ...toRemove.map((r) => unassignRole(member.id, r.id)),
        ]);
        toast("Admin roles updated.", "success");
      }
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save admin.", "error");
    } finally {
      setSaving(false);
    }
  }

  function toggleRole(name: string) {
    setSelectedRoleNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <Modal
      title={isNew ? "Invite Admin" : "Edit Admin"}
      onClose={onClose}
      footer={
        <>
          <button className="btn-cancel" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn-submit" onClick={save} disabled={saving || loadingRoles}>
            {saving ? "Saving…" : isNew ? "Send Invite" : "Save Changes"}
          </button>
        </>
      }
    >
      <div className="form-group">
        <label className="form-label">Name *</label>
        <input
          className="form-input"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          disabled={!isNew}
        />
      </div>
      <div className="form-group">
        <label className="form-label">Email *</label>
        <input
          className="form-input"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={!isNew}
        />
      </div>

      {isNew ? (
        <div className="form-group">
          <label className="form-label">Role</label>
          <select
            className="form-select"
            value={selectedRoleId}
            onChange={(e) => setSelectedRoleId(e.target.value)}
            disabled={loadingRoles}
          >
            {roles.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
            Permissions for this role are defined in Roles &amp; Permissions.
          </p>
        </div>
      ) : (
        <div className="form-group">
          <label className="form-label">Roles</label>
          {loadingRoles ? (
            <p style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>Loading roles…</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {roles.map((r) => (
                <label
                  key={r.id}
                  style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.85rem" }}
                >
                  <input
                    type="checkbox"
                    checked={selectedRoleNames.has(r.name)}
                    onChange={() => toggleRole(r.name)}
                  />
                  {r.name}
                </label>
              ))}
            </div>
          )}
          <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
            Permissions for each role are defined in Roles &amp; Permissions.
          </p>
        </div>
      )}

      {isNew && (
        <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", lineHeight: 1.6 }}>
          An email will be sent with a link to set their password.
        </p>
      )}
    </Modal>
  );
}
