"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { createAdminUser, updateAdminUser } from "@/lib/api/admin-users";
import type { AdminUserDetail, AdminUserSummary } from "@/lib/types/admin-users";

interface UserModalProps {
  user: AdminUserSummary | AdminUserDetail | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function UserModal({ user, onClose, onSaved }: UserModalProps) {
  const { toast } = useToast();
  const isNew = !user;
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [institution, setInstitution] = useState(user?.institution ?? "");
  const [phone, setPhone] = useState((user as AdminUserDetail)?.phone ?? "");
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!fullName.trim() || (isNew && !email.trim())) {
      toast("Name and email are required.", "error");
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        await createAdminUser({
          full_name: fullName.trim(),
          email: email.trim(),
          institution: institution || null,
          phone: phone || null,
        });
      } else {
        await updateAdminUser(user.id, {
          full_name: fullName.trim(),
          institution: institution || null,
          phone: phone || null,
        });
      }
      toast(isNew ? "User created." : "User updated.", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save user.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={isNew ? "Add User" : "Edit User"}
      onClose={onClose}
      footer={
        <>
          <button className="btn-cancel" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn-submit" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save User"}
          </button>
        </>
      }
    >
      <div className="form-group">
        <label className="form-label">Full Name *</label>
        <input className="form-input" value={fullName} onChange={(e) => setFullName(e.target.value)} />
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
          <label className="form-label">Institution</label>
          <input className="form-input" value={institution} onChange={(e) => setInstitution(e.target.value)} />
        </div>
        <div className="form-group">
          <label className="form-label">Phone</label>
          <input className="form-input" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </div>
      </div>
      {isNew && (
        <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", lineHeight: 1.6 }}>
          An email will be sent to this address with a link to set their password.
        </p>
      )}
    </Modal>
  );
}
