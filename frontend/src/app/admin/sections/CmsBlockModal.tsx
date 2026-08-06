"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { updateCmsBlock } from "@/lib/api/admin-cms";
import type { CmsBlockPublic } from "@/lib/types/admin-cms";

interface CmsBlockModalProps {
  block: CmsBlockPublic;
  onClose: () => void;
  onSaved: () => void;
}

export default function CmsBlockModal({ block, onClose, onSaved }: CmsBlockModalProps) {
  const { toast } = useToast();
  const [value, setValue] = useState(block.value ?? "");
  const [isActive, setIsActive] = useState(block.is_active);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await updateCmsBlock(block.id, { value, is_active: isActive });
      toast("Content block saved.", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save block.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={`Edit: ${block.label ?? block.key}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn-cancel" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn-submit" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save Block"}
          </button>
        </>
      }
    >
      <div className="form-group">
        <label className="form-label">Content</label>
        <textarea
          className="form-textarea"
          style={{ minHeight: 140 }}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        {block.key.includes("faq.items") && (
          <div style={{ fontSize: "0.74rem", color: "var(--text-muted)", marginTop: "0.4rem" }}>
            JSON array of <code>{"{q, a}"}</code> pairs — this block powers the Contact page&rsquo;s FAQ list.
          </div>
        )}
        {block.key.endsWith(".body") && (
          <div style={{ fontSize: "0.74rem", color: "var(--text-muted)", marginTop: "0.4rem" }}>
            HTML supported (headings, lists, links, images) — sanitized automatically on save.
          </div>
        )}
      </div>
      <div className="form-group">
        <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          Active (shown on the public site)
        </label>
      </div>
    </Modal>
  );
}
