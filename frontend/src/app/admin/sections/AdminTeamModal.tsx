"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { inviteAdmin, updateAdminTeamMember } from "@/lib/api/admin-team";
import type { AdminTeamMemberPublic } from "@/lib/types/admin-team";

const ADMIN_ROLES = ["Administrator", "Data Manager", "Reviewer", "Content Editor"];

interface AdminTeamModalProps {
  member: AdminTeamMemberPublic | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function AdminTeamModal({ member, onClose, onSaved }: AdminTeamModalProps) {
  const { toast } = useToast();
  const isNew = !member;
  const [name, setName] = useState(member?.name ?? "");
  const [email, setEmail] = useState(member?.email ?? "");
  const [roleLabel, setRoleLabel] = useState(member?.role_label ?? ADMIN_ROLES[0]);
  const [status, setStatus] = useState(member?.status ?? "active");
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!name.trim() || (isNew && !email.trim())) {
      toast("Name and email are required.", "error");
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        await inviteAdmin({ name: name.trim(), email: email.trim(), role_label: roleLabel });
      } else {
        await updateAdminTeamMember(member.id, { name: name.trim(), role_label: roleLabel, status });
      }
      toast(isNew ? "Admin invited." : "Admin updated.", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save admin.", "error");
    } finally {
      setSaving(false);
    }
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
          <button className="btn-submit" onClick={save} disabled={saving}>
            {saving ? "Saving…" : isNew ? "Send Invite" : "Save Changes"}
          </button>
        </>
      }
    >
      <div className="form-group">
        <label className="form-label">Name *</label>
        <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} />
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
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">Role</label>
          <select className="form-select" value={roleLabel ?? ADMIN_ROLES[0]} onChange={(e) => setRoleLabel(e.target.value)}>
            {ADMIN_ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        {!isNew && (
          <div className="form-group">
            <label className="form-label">Status</label>
            <select className="form-select" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="active">Active</option>
              <option value="suspended">Suspended</option>
            </select>
          </div>
        )}
      </div>
      {isNew && (
        <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", lineHeight: 1.6 }}>
          An email will be sent with a link to set their password.
        </p>
      )}
    </Modal>
  );
}
