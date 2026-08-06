"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { createCategory, updateCategory } from "@/lib/api/admin-categories";
import type { CategoryPublic } from "@/lib/types/admin-categories";

const COLOR_TAGS = ["cat-Environmental", "cat-Pollution", "cat-Water", "cat-Hydrological", "cat-Atmospheric", "cat-Model"];

interface CategoryModalProps {
  category: CategoryPublic | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function CategoryModal({ category, onClose, onSaved }: CategoryModalProps) {
  const { toast } = useToast();
  const isNew = !category;
  const [name, setName] = useState(category?.name ?? "");
  const [description, setDescription] = useState(category?.description ?? "");
  const [colorTag, setColorTag] = useState(category?.color_tag ?? COLOR_TAGS[0]);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!name.trim()) {
      toast("Category name is required.", "error");
      return;
    }
    setSaving(true);
    try {
      const payload = { name: name.trim(), description: description || null, color_tag: colorTag };
      if (isNew) {
        await createCategory(payload);
      } else {
        await updateCategory(category.id, payload);
      }
      toast(isNew ? "Category created." : "Category updated.", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save category.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={isNew ? "Add Category" : "Edit Category"}
      onClose={onClose}
      footer={
        <>
          <button className="btn-cancel" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn-submit" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save Category"}
          </button>
        </>
      }
    >
      <div className="form-group">
        <label className="form-label">Name *</label>
        <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="form-group">
        <label className="form-label">Description</label>
        <textarea className="form-textarea" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="form-group">
        <label className="form-label">Color Tag</label>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          {COLOR_TAGS.map((c) => (
            <button
              key={c}
              type="button"
              className={`cat-pill ${c}`}
              style={{ cursor: "pointer", border: colorTag === c ? "2px solid var(--accent)" : undefined }}
              onClick={() => setColorTag(c)}
            >
              {c.replace("cat-", "")}
            </button>
          ))}
        </div>
      </div>
    </Modal>
  );
}
