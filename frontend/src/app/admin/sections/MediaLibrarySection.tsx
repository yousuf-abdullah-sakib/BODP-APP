"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/context/ConfirmContext";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { deleteMediaFile, getMediaFiles, uploadMediaFile } from "@/lib/api/admin-media";
import type { MediaFilePublic } from "@/lib/types/admin-media";

const TYPE_FILTERS: { label: string; prefix: string }[] = [
  { label: "All", prefix: "" },
  { label: "Images", prefix: "image/" },
  { label: "Documents", prefix: "application/" },
];

function iconFor(mimeType: string | null): string {
  if (!mimeType) return "📁";
  if (mimeType.startsWith("image/")) return "🖼️";
  if (mimeType === "application/pdf") return "📄";
  if (mimeType.includes("spreadsheet") || mimeType === "text/csv") return "📊";
  return "📁";
}

function formatSize(bytes: number | null): string {
  if (bytes == null) return "—";
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function MediaLibrarySection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [media, setMedia] = useState<MediaFilePublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [uploading, setUploading] = useState(false);

  function refetch() {
    setLoading(true);
    getMediaFiles({ search: search || undefined, mime_prefix: typeFilter || undefined })
      .then(setMedia)
      .catch(() => setError("Failed to load media library."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, typeFilter]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await uploadMediaFile(file);
      }
      toast(`${files.length} file(s) uploaded.`, "success");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to upload file.", "error");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function handleDelete(m: MediaFilePublic) {
    const ok = await confirm({
      title: "Delete File",
      message: (
        <>
          Delete <b>{m.file_name}</b> from the media library?
        </>
      ),
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteMediaFile(m.id);
      toast(`${m.file_name} deleted.`, "info");
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to delete file.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Media Library</div>
          <div className="dash-sub">Images and files used across the site.</div>
        </div>
        <label className="btn-primary" style={{ cursor: uploading ? "wait" : "pointer" }}>
          {uploading ? "Uploading…" : "⬆ Upload"}
          <input
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={handleUpload}
            disabled={uploading}
          />
        </label>
      </div>

      <div className="filter-toolbar">
        <input
          type="text"
          placeholder="Search files…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          {TYPE_FILTERS.map((f) => (
            <option key={f.prefix} value={f.prefix}>
              {f.label}
            </option>
          ))}
        </select>
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading media…</p>
        </div>
      ) : media.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">🖼️</div>
          <p>No media files yet.</p>
        </div>
      ) : (
        <div className="dl-grid">
          {media.map((m) => (
            <div className="dl-card" key={m.id} style={{ textAlign: "center" }}>
              {m.mime_type?.startsWith("image/") ? (
                <img
                  src={m.url}
                  alt={m.alt_text ?? m.file_name}
                  style={{ width: "100%", height: 90, objectFit: "cover", borderRadius: 8, marginBottom: "0.6rem" }}
                />
              ) : (
                <div style={{ fontSize: "2rem", marginBottom: "0.6rem" }}>{iconFor(m.mime_type)}</div>
              )}
              <div
                style={{
                  fontSize: "0.8rem",
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  wordBreak: "break-word",
                }}
              >
                {m.title || m.file_name}
              </div>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                {formatSize(m.size_bytes)} · {new Date(m.uploaded_at).toLocaleDateString()}
              </div>
              <button
                className="btn-icon-sm danger"
                style={{ marginTop: "0.7rem" }}
                title="Delete"
                onClick={() => handleDelete(m)}
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
