"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { deleteCategory, getCategories } from "@/lib/api/admin-categories";
import CategoryModal from "./CategoryModal";
import type { CategoryPublic } from "@/lib/types/admin-categories";

export default function DatasetCategoriesSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [categories, setCategories] = useState<CategoryPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<CategoryPublic | null | "new">(null);

  function refetch() {
    setLoading(true);
    getCategories()
      .then(setCategories)
      .catch(() => setError("Failed to load categories."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
  }, []);

  async function handleDelete(cat: CategoryPublic) {
    if (cat.dataset_count > 0) {
      const ok = await confirm({
        title: "Delete Category",
        message: (
          <>
            <b>{cat.name}</b> is currently used by <b>{cat.dataset_count}</b> dataset(s). Deleting it will leave
            those datasets uncategorized. Continue?
          </>
        ),
        confirmLabel: "Delete Anyway",
        danger: true,
      });
      if (!ok) return;
    }
    try {
      await deleteCategory(cat.id);
      toast(`${cat.name} deleted.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to delete category.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Dataset Categories</div>
          <div className="dash-sub">Manage the category taxonomy used across the catalog.</div>
        </div>
        <button className="btn-primary" onClick={() => setEditing("new")}>
          + New Category
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
          <p>Loading categories…</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th>Description</th>
                <th>Datasets</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((c) => (
                <tr key={c.id}>
                  <td>
                    <span className={`cat-pill ${c.color_tag}`}>{c.name}</span>
                  </td>
                  <td style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>{c.description}</td>
                  <td>{c.dataset_count}</td>
                  <td>
                    <div className="flex-gap">
                      <button className="btn-icon-sm" title="Edit" onClick={() => setEditing(c)}>
                        ✎
                      </button>
                      <button className="btn-icon-sm danger" title="Delete" onClick={() => handleDelete(c)}>
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
        <CategoryModal category={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refetch} />
      )}
    </>
  );
}
