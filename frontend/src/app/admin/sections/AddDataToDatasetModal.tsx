"use client";

import { useEffect, useRef, useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getUploadStatus, uploadDatasetFile } from "@/lib/api/admin-datasets";

const POLL_INTERVAL_MS = 1500;

interface AddDataToDatasetModalProps {
  dataset: { id: string; title: string };
  onClose: () => void;
  onUploaded: () => void;
}

export default function AddDataToDatasetModal({ dataset, onClose, onUploaded }: AddDataToDatasetModalProps) {
  const { toast } = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) setFile(f);
  }

  function pollUpload(uploadId: string) {
    pollRef.current = setInterval(async () => {
      try {
        const upload = await getUploadStatus(uploadId);
        if (upload.status === "complete") {
          if (pollRef.current) clearInterval(pollRef.current);
          setUploading(false);
          setStatusText("Complete.");
          toast(`${upload.file_name} uploaded and processed successfully.`, "success");
          onUploaded();
          onClose();
        } else if (upload.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          setUploading(false);
          setStatusText(upload.error_message ?? "Upload failed.");
          toast(upload.error_message ?? "Upload processing failed.", "error");
        } else {
          setStatusText(`Status: ${upload.status}…`);
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
        setUploading(false);
        setStatusText("Failed to check upload status.");
      }
    }, POLL_INTERVAL_MS);
  }

  async function submit() {
    if (!file) return;
    setUploading(true);
    setStatusText("Uploading…");
    try {
      const result = await uploadDatasetFile(dataset.id, file);
      setStatusText("Processing…");
      pollUpload(result.upload.id);
    } catch (err) {
      setUploading(false);
      setStatusText(null);
      toast(err instanceof ApiError ? err.message : "Failed to upload file.", "error");
    }
  }

  return (
    <Modal
      title={`Add Data to "${dataset.title}"`}
      onClose={onClose}
      footer={
        <>
          <button className="btn-ghost-sm" onClick={onClose} disabled={uploading}>
            Cancel
          </button>
          <button className="btn-primary" onClick={submit} disabled={!file || uploading}>
            {uploading ? "Uploading…" : "⬆ Upload"}
          </button>
        </>
      }
    >
      <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "1rem", lineHeight: 1.6 }}>
        Append new files or records to this existing dataset. The dataset&apos;s own metadata (title, category,
        description) stays unchanged — use Edit for that.
      </p>
      <div className="form-group">
        <label className="form-label">Data File *</label>
        <label
          style={{
            border: "2px dashed var(--border)",
            borderRadius: 8,
            padding: "1.2rem",
            textAlign: "center",
            cursor: uploading ? "default" : "pointer",
            fontSize: "0.82rem",
            color: "var(--text-muted)",
            display: "block",
          }}
        >
          📎 Click to choose a CSV / NetCDF / .mat file
          <input type="file" style={{ display: "none" }} onChange={handleFileChange} disabled={uploading} />
          {file && <div style={{ color: "var(--accent)", marginTop: "0.4rem" }}>{file.name}</div>}
        </label>
        {statusText && (
          <div style={{ marginTop: "0.6rem", fontSize: "0.78rem", color: "var(--text-muted)" }}>{statusText}</div>
        )}
      </div>
    </Modal>
  );
}
