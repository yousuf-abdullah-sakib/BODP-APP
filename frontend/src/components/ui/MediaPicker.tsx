"use client";

import { useEffect, useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getMediaFiles, uploadMediaFile } from "@/lib/api/admin-media";
import type { MediaFilePublic } from "@/lib/types/admin-media";

interface MediaPickerProps {
  onClose: () => void;
  onSelect: (media: MediaFilePublic) => void;
}

/** Shared "select an existing Media Library file, or upload a new one"
 * widget — Blog featured images, About Team photos, and any future
 * content-image field all pick through this same picker rather than
 * uploading an independent copy (Master Plan §3 Phase 9 task 4). */
export default function MediaPicker({ onClose, onSelect }: MediaPickerProps) {
  const { toast } = useToast();
  const [media, setMedia] = useState<MediaFilePublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  function refetch() {
    setLoading(true);
    getMediaFiles({ mime_prefix: "image/" })
      .then(setMedia)
      .catch(() => toast("Failed to load media library.", "error"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const uploaded = await uploadMediaFile(file);
      onSelect(uploaded);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to upload file.", "error");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  return (
    <Modal title="Select Image" onClose={onClose} large>
      <div className="dash-header" style={{ marginBottom: "1rem" }}>
        <div className="dash-sub">Choose an existing file from the Media Library, or upload a new one.</div>
        <label className="btn-primary" style={{ cursor: uploading ? "wait" : "pointer" }}>
          {uploading ? "Uploading…" : "⬆ Upload New"}
          <input type="file" accept="image/*" style={{ display: "none" }} onChange={handleUpload} disabled={uploading} />
        </label>
      </div>

      {loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading media…</p>
        </div>
      ) : media.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">🖼️</div>
          <p>No images yet — upload one above.</p>
        </div>
      ) : (
        <div className="dl-grid">
          {media.map((m) => (
            <div
              className="dl-card"
              key={m.id}
              style={{ textAlign: "center", cursor: "pointer" }}
              onClick={() => onSelect(m)}
            >
              <img
                src={m.url}
                alt={m.alt_text ?? m.file_name}
                style={{ width: "100%", height: 90, objectFit: "cover", borderRadius: 8, marginBottom: "0.4rem" }}
              />
              <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)", wordBreak: "break-word" }}>
                {m.title || m.file_name}
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
