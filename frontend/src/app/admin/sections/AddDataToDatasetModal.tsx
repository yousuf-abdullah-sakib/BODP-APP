"use client";

import { useEffect, useRef, useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import { ApiError } from "@/lib/api/client";
import {
  cancelUpload,
  completeMultipartUpload,
  getUploadStatus,
  initiateMultipartUpload,
  markPartUploaded,
  presignUploadPart,
  uploadDatasetFile,
  uploadPartDirect,
} from "@/lib/api/admin-datasets";

const POLL_INTERVAL_MS = 1500;

// Files at or above this size go through direct-to-MinIO multipart upload
// (Phase 1) instead of the single-request path — proxying a genuinely
// large file through this backend process wastes its bandwidth/CPU for no
// benefit once a real multipart alternative exists. Files below it keep
// using the original, simpler single-request endpoint unchanged.
const MULTIPART_THRESHOLD_BYTES = 20 * 1024 * 1024;

interface AddDataToDatasetModalProps {
  dataset: { id: string; title: string };
  onClose: () => void;
  onUploaded: () => void;
}

export default function AddDataToDatasetModal({ dataset, onClose, onUploaded }: AddDataToDatasetModalProps) {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  // Upload progress (bytes actually transferred to storage) and
  // processing progress (ingestion sub-stage) are tracked and rendered
  // as two entirely separate figures — never conflated into one bar,
  // per the Phase 1 requirement.
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [uploadedBytes, setUploadedBytes] = useState<number | null>(null);
  const [totalBytes, setTotalBytes] = useState<number | null>(null);
  const [processingStage, setProcessingStage] = useState<string | null>(null);
  const [processingPct, setProcessingPct] = useState<number | null>(null);
  const [statusText, setStatusText] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const currentUploadId = useRef<string | null>(null);
  const abortRef = useRef(false);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      abortRef.current = true;
    };
  }, []);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) setFile(f);
  }

  function resetProgress() {
    setUploadPct(null);
    setUploadedBytes(null);
    setTotalBytes(null);
    setProcessingStage(null);
    setProcessingPct(null);
  }

  function pollProcessing(uploadId: string) {
    pollRef.current = setInterval(async () => {
      try {
        const upload = await getUploadStatus(uploadId);
        setProcessingStage(upload.progress_stage);
        setProcessingPct(upload.progress_pct);

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
        } else if (upload.status === "cancelled") {
          if (pollRef.current) clearInterval(pollRef.current);
          setUploading(false);
          setStatusText("Cancelled.");
          toast("Upload cancelled.", "info");
        } else {
          setStatusText(`Processing: ${upload.progress_stage ?? upload.status}…`);
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
        setUploading(false);
        setStatusText("Failed to check upload status.");
      }
    }, POLL_INTERVAL_MS);
  }

  async function submitSmallFile(f: File) {
    setStatusText("Uploading…");
    const result = await uploadDatasetFile(dataset.id, f);
    currentUploadId.current = result.upload.id;
    setStatusText("Processing…");
    pollProcessing(result.upload.id);
  }

  async function submitLargeFile(f: File) {
    setStatusText("Starting upload…");
    const { upload, total_parts, part_size_bytes } = await initiateMultipartUpload(
      dataset.id,
      f.name,
      f.size
    );
    currentUploadId.current = upload.id;
    setTotalBytes(f.size);
    setUploadedBytes(0);
    setUploadPct(0);

    let bytesDone = 0;
    for (let partNumber = 1; partNumber <= total_parts; partNumber++) {
      if (abortRef.current) return;

      const start = (partNumber - 1) * part_size_bytes;
      const end = Math.min(start + part_size_bytes, f.size);
      const blob = f.slice(start, end);

      const { upload_url } = await presignUploadPart(upload.id, partNumber);
      await uploadPartDirect(upload_url, blob);
      await markPartUploaded(upload.id, partNumber, blob.size);

      bytesDone += blob.size;
      setUploadedBytes(bytesDone);
      setUploadPct(Math.round((bytesDone / f.size) * 100));
      setStatusText(`Uploading… ${Math.round((bytesDone / f.size) * 100)}%`);
    }

    if (abortRef.current) return;

    setStatusText("Finalizing upload…");
    const { upload: completedUpload } = await completeMultipartUpload(upload.id);
    setStatusText("Processing…");
    pollProcessing(completedUpload.id);
  }

  async function submit() {
    if (!file) return;
    setUploading(true);
    resetProgress();
    abortRef.current = false;
    try {
      if (file.size >= MULTIPART_THRESHOLD_BYTES) {
        await submitLargeFile(file);
      } else {
        await submitSmallFile(file);
      }
    } catch (err) {
      setUploading(false);
      setStatusText(null);
      toast(err instanceof ApiError ? err.message : "Failed to upload file.", "error");
    }
  }

  async function handleCancel() {
    const uploadId = currentUploadId.current;
    if (!uploadId) {
      // Nothing has been initiated/uploaded yet — just close, matching
      // the original modal's behavior for the pre-submit state.
      onClose();
      return;
    }
    const ok = await confirm({
      title: "Cancel Upload",
      message:
        "Cancel this upload? Any data already transferred will be discarded and no dataset file will be created.",
      confirmLabel: "Cancel Upload",
      danger: true,
    });
    if (!ok) return;

    abortRef.current = true;
    setCancelling(true);
    if (pollRef.current) clearInterval(pollRef.current);
    try {
      await cancelUpload(uploadId);
      setStatusText("Cancelled.");
      toast("Upload cancelled.", "info");
      setUploading(false);
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to cancel upload.", "error");
    } finally {
      setCancelling(false);
    }
  }

  return (
    <Modal
      title={`Add Data to "${dataset.title}"`}
      onClose={uploading ? handleCancel : onClose}
      footer={
        <>
          <button className="btn-ghost-sm" onClick={handleCancel} disabled={cancelling}>
            {uploading ? (cancelling ? "Cancelling…" : "Cancel Upload") : "Cancel"}
          </button>
          <button className="btn-primary" onClick={submit} disabled={!file || uploading}>
            {uploading ? "Uploading…" : "⬆ Upload"}
          </button>
        </>
      }
    >
      <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "1rem", lineHeight: 1.6 }}>
        Append new files or records to this existing dataset. The dataset&apos;s own metadata (title, category,
        description) stays unchanged — use Edit for that. Large files upload directly to storage in chunks.
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
          📎 Click to choose a CSV / NetCDF / .mat / GeoTIFF file
          <input type="file" style={{ display: "none" }} onChange={handleFileChange} disabled={uploading} />
          {file && <div style={{ color: "var(--accent)", marginTop: "0.4rem" }}>{file.name}</div>}
        </label>

        {uploadPct !== null && (
          <div style={{ marginTop: "0.8rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "var(--text-muted)" }}>
              <span>Upload: {uploadPct}%</span>
              {totalBytes !== null && uploadedBytes !== null && (
                <span>
                  {formatBytes(uploadedBytes)} / {formatBytes(totalBytes)}
                </span>
              )}
            </div>
            <div style={{ height: 6, background: "var(--border)", borderRadius: 3, marginTop: "0.3rem", overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  width: `${uploadPct}%`,
                  background: "var(--accent)",
                  transition: "width 0.2s",
                }}
              />
            </div>
          </div>
        )}

        {processingStage && (
          <div style={{ marginTop: "0.8rem" }}>
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              Processing: {processingStage}
              {processingPct !== null ? ` ${processingPct}%` : "…"}
            </div>
            <div style={{ height: 6, background: "var(--border)", borderRadius: 3, marginTop: "0.3rem", overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  width: `${processingPct ?? 0}%`,
                  background: "var(--accent-dark, var(--accent))",
                  transition: "width 0.2s",
                }}
              />
            </div>
          </div>
        )}

        {statusText && (
          <div style={{ marginTop: "0.6rem", fontSize: "0.78rem", color: "var(--text-muted)" }}>{statusText}</div>
        )}
      </div>
    </Modal>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
