"use client";

import { useEffect, useState } from "react";
import CategoryPill from "@/components/ui/CategoryPill";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import {
  archiveDataset,
  getAdminDataset,
  getAdminDatasets,
  permanentlyDeleteDataset,
  publishDataset,
  unarchiveDataset,
  unpublishDataset,
} from "@/lib/api/admin-datasets";
import AddDataToDatasetModal from "./AddDataToDatasetModal";
import DatasetModal from "./DatasetModal";
import type { DatasetAdminDetail, DatasetAdminSummary } from "@/lib/types/admin-datasets";

export default function AdminDatasetsSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [datasets, setDatasets] = useState<DatasetAdminSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<DatasetAdminDetail | null | "new">(null);
  const [addingDataTo, setAddingDataTo] = useState<DatasetAdminSummary | null>(null);
  const [loadingDetail, setLoadingDetail] = useState<string | null>(null);

  function refetch() {
    setLoading(true);
    getAdminDatasets({ search: search || undefined })
      .then(setDatasets)
      .catch(() => setError("Failed to load datasets."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  async function openEdit(d: DatasetAdminSummary) {
    setLoadingDetail(d.id);
    try {
      const detail = await getAdminDataset(d.id);
      setEditing(detail);
    } catch {
      toast("Failed to load dataset details.", "error");
    } finally {
      setLoadingDetail(null);
    }
  }

  async function handlePublishToggle(d: DatasetAdminSummary) {
    try {
      if (d.status === "published") {
        await unpublishDataset(d.id);
        toast(`${d.title} is now draft.`, "success");
      } else {
        await publishDataset(d.id);
        toast(`${d.title} is now published.`, "success");
      }
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to change publish status.", "error");
    }
  }

  async function handleArchive(d: DatasetAdminSummary) {
    const detail = await getAdminDataset(d.id).catch(() => null);
    const activeGrantCount = detail?.active_grant_count ?? 0;
    const ok = await confirm({
      title: "Archive Dataset",
      message: (
        <>
          Archive <b>&ldquo;{d.title}&rdquo;</b>? This will remove it from the public catalog
          {activeGrantCount > 0 && (
            <>
              {" "}
              and revoke access for <b>{activeGrantCount}</b> user(s) who currently have this dataset granted
            </>
          )}
          .
        </>
      ),
      confirmLabel: "Archive",
      danger: false,
    });
    if (!ok) return;
    try {
      await archiveDataset(d.id);
      toast(`${d.title} archived.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to archive dataset.", "error");
    }
  }

  async function handleUnarchive(d: DatasetAdminSummary) {
    try {
      await unarchiveDataset(d.id);
      toast(`${d.title} unarchived.`, "success");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to unarchive dataset.", "error");
    }
  }

  async function handlePermanentDelete(d: DatasetAdminSummary) {
    const detail = await getAdminDataset(d.id).catch(() => null);
    const activeGrantCount = detail?.active_grant_count ?? 0;
    const ok = await confirm({
      title: "Permanently Delete Dataset",
      message: (
        <>
          Permanently delete <b>&ldquo;{d.title}&rdquo;</b>? This cannot be undone.
          {activeGrantCount > 0 && (
            <>
              {" "}
              This will also revoke access for <b>{activeGrantCount}</b> user(s).
            </>
          )}
        </>
      ),
      confirmLabel: "Delete Permanently",
      danger: true,
    });
    if (!ok) return;
    try {
      await permanentlyDeleteDataset(d.id);
      toast(`${d.title} permanently deleted.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to delete dataset.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Datasets</div>
          <div className="dash-sub">Manage the dataset catalog.</div>
        </div>
        <button className="btn-primary" onClick={() => setEditing("new")}>
          + New Dataset
        </button>
      </div>

      <div className="filter-toolbar">
        <input type="text" placeholder="Search datasets…" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading datasets…</p>
        </div>
      ) : datasets.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">🗄️</div>
          <p>No datasets found.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Category</th>
                <th>Location</th>
                <th>Records</th>
                <th>Updated</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((d) => (
                <tr key={d.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{d.title}</div>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>{d.code}</div>
                  </td>
                  <td>
                    <CategoryPill category={d.category_name} />
                  </td>
                  <td style={{ fontSize: "0.82rem" }}>{d.location || "—"}</td>
                  <td>{d.record_count.toLocaleString()}</td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                    {new Date(d.updated_at).toLocaleDateString()}
                  </td>
                  <td>
                    <span
                      className={`badge badge-${d.status === "published" ? "published" : d.status === "draft" ? "draft" : "revoked"}`}
                    >
                      {d.status === "published" ? "● Published" : d.status === "draft" ? "◐ Draft" : "Archived"}
                    </span>
                  </td>
                  <td>
                    <div className="flex-gap">
                      <button
                        className="btn-icon-sm"
                        title="Edit dataset metadata"
                        onClick={() => openEdit(d)}
                        disabled={loadingDetail === d.id}
                      >
                        ✎
                      </button>
                      <button className="btn-icon-sm" title="Add data to this dataset" onClick={() => setAddingDataTo(d)}>
                        ⬆
                      </button>
                      {d.status === "archived" ? (
                        <button className="btn-icon-sm" title="Unarchive" onClick={() => handleUnarchive(d)}>
                          ↺
                        </button>
                      ) : (
                        <button
                          className="btn-icon-sm"
                          title={d.status === "published" ? "Unpublish" : "Publish"}
                          onClick={() => handlePublishToggle(d)}
                        >
                          {d.status === "published" ? "◐" : "●"}
                        </button>
                      )}
                      {d.status !== "archived" && (
                        <button className="btn-icon-sm" title="Archive" onClick={() => handleArchive(d)}>
                          🗄
                        </button>
                      )}
                      <button
                        className="btn-icon-sm danger"
                        title="Permanently delete"
                        onClick={() => handlePermanentDelete(d)}
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
        <DatasetModal dataset={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refetch} />
      )}
      {addingDataTo && (
        <AddDataToDatasetModal dataset={addingDataTo} onClose={() => setAddingDataTo(null)} onUploaded={refetch} />
      )}
    </>
  );
}
