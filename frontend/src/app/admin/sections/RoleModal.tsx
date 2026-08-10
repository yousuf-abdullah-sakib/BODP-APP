"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { createRole, updateRole } from "@/lib/api/admin-roles";
import { PERMISSION_LIST } from "@/lib/types/admin-roles";
import type { RolePublic } from "@/lib/types/admin-roles";

// Mirrors backend/app/services/admin_roles_service.py's _SYSTEM_ROLE_NAMES.
const SYSTEM_ROLE_NAMES = new Set(["Administrator", "User", "Data Manager", "Reviewer", "Content Editor"]);

interface RoleModalProps {
  role: RolePublic | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function RoleModal({ role, onClose, onSaved }: RoleModalProps) {
  const { toast } = useToast();
  const isNew = !role;
  const isSystemRole = !!role && SYSTEM_ROLE_NAMES.has(role.name);
  // Administrator always holds every permission — enforced server-side too
  // (admin_roles_service.update_role coerces this regardless), locked here
  // so the checkboxes don't even suggest it's changeable.
  const isAdministrator = role?.name === "Administrator";
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [permissions, setPermissions] = useState<Set<string>>(new Set(role?.permissions ?? []));
  const [saving, setSaving] = useState(false);

  function toggle(p: string) {
    setPermissions((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  }

  async function save() {
    if (!name.trim()) {
      toast("Role name is required.", "error");
      return;
    }
    setSaving(true);
    try {
      const payload = { name: name.trim(), description: description || null, permissions: Array.from(permissions) };
      if (isNew) {
        await createRole(payload);
      } else {
        await updateRole(role.id, payload);
      }
      toast(isNew ? "Role created." : "Role updated.", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save role.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={isNew ? "Add Role" : "Edit Role"}
      onClose={onClose}
      footer={
        <>
          <button className="btn-cancel" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn-submit" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save Role"}
          </button>
        </>
      }
    >
      <div className="form-group">
        <label className="form-label">Role Name *</label>
        <input
          className="form-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={isSystemRole}
        />
        {isSystemRole && (
          <p style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
            This role&apos;s name can&apos;t be changed.
          </p>
        )}
      </div>
      <div className="form-group">
        <label className="form-label">Description</label>
        <textarea className="form-textarea" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="form-group">
        <label className="form-label">Permissions</label>
        {isAdministrator && (
          <p style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
            Administrator always has every permission.
          </p>
        )}
        <div className="req-dataset-list">
          {PERMISSION_LIST.map((p) => (
            <label
              key={p}
              className="req-dataset-row"
              style={{ cursor: isAdministrator ? "not-allowed" : "pointer" }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
                <input
                  type="checkbox"
                  checked={isAdministrator ? true : permissions.has(p)}
                  onChange={() => toggle(p)}
                  disabled={isAdministrator}
                />
                {p}
              </span>
            </label>
          ))}
        </div>
      </div>
    </Modal>
  );
}
