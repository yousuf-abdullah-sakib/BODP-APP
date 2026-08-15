"use client";

import { useEffect, useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { useUploadTracker } from "@/context/UploadTrackerContext";
import { ApiError } from "@/lib/api/client";
import { startBulkImport, validateBulkImport } from "@/lib/api/admin-bulk-import";
import { getAdminDatasets } from "@/lib/api/admin-datasets";
import type { BulkImportValidateResponse } from "@/lib/types/admin-bulk-import";
import type { DatasetAdminSummary } from "@/lib/types/admin-datasets";

interface BulkImportModalProps {
  onClose: () => void;
  onImported: () => void;
}

/** Admin Panel Bulk Import (production-readiness follow-up) — imports a
 * file already present on the server/NAS/storage the backend can reach,
 * without routing the file through the browser at all. The existing CLI
 * script (app/scripts/bulk_import.py) remains the tool of record for
 * operators with direct container/shell access; this is the equivalent
 * workflow for an admin working entirely from the browser. Validate is a
 * real dry-run (no writes); Import dispatches the actual transfer as a
 * background Celery task and hands off to the same UploadTrackerContext
 * every other upload path already uses, so progress/minimize/cancel all
 * work identically. */
export default function BulkImportModal({ onClose, onImported }: BulkImportModalProps) {
  const { toast } = useToast();
  const { trackServerUpload } = useUploadTracker();

  const [datasets, setDatasets] = useState<DatasetAdminSummary[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [validating, setValidating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [plan, setPlan] = useState<BulkImportValidateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAdminDatasets()
      .then(setDatasets)
      .catch(() => setDatasets([]));
  }, []);

  function resetPlan() {
    setPlan(null);
    setError(null);
  }

  async function handleValidate() {
    if (!datasetId || !sourcePath.trim()) return;
    setValidating(true);
    setError(null);
    try {
      const result = await validateBulkImport({ dataset_id: datasetId, source_path: sourcePath.trim() });
      setPlan(result);
    } catch (err) {
      setPlan(null);
      setError(err instanceof ApiError ? err.message : "Failed to validate file.");
    } finally {
      setValidating(false);
    }
  }

  async function handleImport() {
    if (!plan) return;
    setStarting(true);
    try {
      const { upload } = await startBulkImport({ dataset_id: datasetId, source_path: sourcePath.trim() });
      trackServerUpload({
        uploadId: upload.id,
        datasetId,
        datasetTitle: plan.dataset_title,
        fileName: plan.filename,
        onUploaded: onImported,
      });
      toast(`Bulk import started for ${plan.filename}.`, "info");
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to start bulk import.", "error");
    } finally {
      setStarting(false);
    }
  }

  function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  return (
    <Modal
      title="Bulk Import from Server / NAS"
      onClose={onClose}
      large
      footer={
        <>
          <button className="btn-ghost-sm" onClick={onClose} disabled={starting}>
            Cancel
          </button>
          {plan ? (
            <button className="btn-primary" onClick={handleImport} disabled={starting}>
              {starting ? "Starting…" : "⬆ Start Import"}
            </button>
          ) : (
            <button className="btn-primary" onClick={handleValidate} disabled={!datasetId || !sourcePath.trim() || validating}>
              {validating ? "Validating…" : "🔍 Validate"}
            </button>
          )}
        </>
      }
    >
      <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "1rem", lineHeight: 1.6 }}>
        Import a file already present on the server or a mounted NAS path directly into storage — the file never
        travels through your browser. The path must be reachable from inside the backend/worker containers. The
        transfer runs in the background; you can track its progress the same way as any other upload.
      </p>

      <div className="form-group">
        <label className="form-label">Target Dataset *</label>
        <select
          className="form-select"
          value={datasetId}
          onChange={(e) => {
            setDatasetId(e.target.value);
            resetPlan();
          }}
          disabled={!!plan}
        >
          <option value="">Select a dataset…</option>
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.title} ({d.code})
            </option>
          ))}
        </select>
      </div>

      <div className="form-group">
        <label className="form-label">Server File Path *</label>
        <input
          className="form-input"
          value={sourcePath}
          onChange={(e) => {
            setSourcePath(e.target.value);
            resetPlan();
          }}
          placeholder="/data/nas/incoming/large_dataset.nc"
          disabled={!!plan}
        />
      </div>

      {error && (
        <div className="req-letter-box note-danger" style={{ marginTop: "0.9rem" }}>
          <b style={{ color: "var(--red)" }}>Validation failed:</b> {error}
        </div>
      )}

      {plan && (
        <div className="panel" style={{ marginTop: "1rem" }}>
          <div className="panel-head">
            <span className="panel-title">Import Plan</span>
            <button className="btn-ghost-sm" onClick={resetPlan}>
              ✎ Edit
            </button>
          </div>
          <div className="panel-body" style={{ fontSize: "0.82rem", display: "flex", flexDirection: "column", gap: "0.4rem" }}>
            <div>
              <b>Dataset:</b> {plan.dataset_title}
            </div>
            <div>
              <b>File:</b> {plan.filename} ({plan.extension.toUpperCase()})
            </div>
            <div>
              <b>Size:</b> {formatBytes(plan.size_bytes)}
            </div>
            <div style={{ color: "var(--text-muted)" }}>
              <b>Target:</b> {plan.target_bucket}/{plan.target_key}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
