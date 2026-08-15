"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useUploadTracker } from "@/context/UploadTrackerContext";

interface AddDataToDatasetModalProps {
  dataset: { id: string; title: string };
  onClose: () => void;
  onUploaded: () => void;
}

/** Pre-upload file picker only — once a file is chosen and submitted, the
 * actual upload/progress/cancel/minimize experience is owned entirely by
 * UploadTrackerContext's UploadDetailModal, not this component, so it
 * survives regardless of what this modal or the section that opened it
 * does afterward. */
export default function AddDataToDatasetModal({ dataset, onClose, onUploaded }: AddDataToDatasetModalProps) {
  const { startUpload } = useUploadTracker();
  const [file, setFile] = useState<File | null>(null);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) setFile(f);
  }

  function submit() {
    if (!file) return;
    startUpload({ datasetId: dataset.id, datasetTitle: dataset.title, file, onUploaded });
    onClose();
  }

  return (
    <Modal
      title={`Add Data to "${dataset.title}"`}
      onClose={onClose}
      footer={
        <>
          <button className="btn-ghost-sm" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={submit} disabled={!file}>
            ⬆ Upload
          </button>
        </>
      }
    >
      <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "1rem", lineHeight: 1.6 }}>
        Append new files or records to this existing dataset. The dataset&apos;s own metadata (title, category,
        description) stays unchanged — use Edit for that. Large files upload directly to storage in chunks. You can
        minimize the upload window and keep using the Admin Panel while a large upload/processing job continues.
      </p>
      <div className="form-group">
        <label className="form-label">Data File *</label>
        <label
          style={{
            border: "2px dashed var(--border)",
            borderRadius: 8,
            padding: "1.2rem",
            textAlign: "center",
            cursor: "pointer",
            fontSize: "0.82rem",
            color: "var(--text-muted)",
            display: "block",
          }}
        >
          📎 Click to choose a CSV / NetCDF / .mat / GeoTIFF file
          <input type="file" style={{ display: "none" }} onChange={handleFileChange} />
          {file && <div style={{ color: "var(--accent)", marginTop: "0.4rem" }}>{file.name}</div>}
        </label>
      </div>
    </Modal>
  );
}
