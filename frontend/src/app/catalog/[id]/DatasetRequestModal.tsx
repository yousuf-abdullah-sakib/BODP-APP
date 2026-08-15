"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { submitRequest } from "@/lib/api/requests";
import { ApiError } from "@/lib/api/client";
import type { DatasetDetail } from "@/lib/types/catalog";
import type { SpatialBounds } from "@/lib/geo/spatialAoi";

interface DatasetRequestModalProps {
  dataset: DatasetDetail;
  criteria: {
    parameters: string[];
    dateFrom: string;
    dateTo: string;
    bounds: SpatialBounds | null;
  };
  onClose: () => void;
  onSubmitted: () => void;
}

export default function DatasetRequestModal({ dataset, criteria, onClose, onSubmitted }: DatasetRequestModalProps) {
  const { toast } = useToast();
  const [letter, setLetter] = useState("");
  const [fileName, setFileName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const hasCriteria = criteria.parameters.length > 0 || criteria.dateFrom || criteria.dateTo || criteria.bounds;

  async function submit() {
    if (!letter.trim() || letter.trim().length < 50) {
      toast("Please write a detailed justification (min 50 characters).", "error");
      return;
    }
    setSubmitting(true);
    try {
      await submitRequest({
        datasetId: dataset.id,
        justification: letter.trim(),
        searchCriteria: hasCriteria
          ? {
              parameters: criteria.parameters.length > 0 ? criteria.parameters : undefined,
              date_from: criteria.dateFrom || undefined,
              date_to: criteria.dateTo || undefined,
              bounds: criteria.bounds
                ? {
                    lat_min: criteria.bounds.latMin,
                    lat_max: criteria.bounds.latMax,
                    lon_min: criteria.bounds.lonMin,
                    lon_max: criteria.bounds.lonMax,
                  }
                : undefined,
            }
          : undefined,
        file,
      });
      onSubmitted();
      toast("✅ Request submitted! You'll be notified once an admin reviews it.", "success");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to submit request.", "error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      title="📋 Request Dataset Access"
      onClose={onClose}
      footer={
        <>
          <button className="btn-cancel" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn-submit" onClick={submit} disabled={submitting}>
            {submitting ? "Submitting…" : "Submit Request →"}
          </button>
        </>
      }
    >
      <div className="modal-section">
        <h4>Dataset</h4>
        <div className="selected-list">
          <div className="selected-item">
            <span>
              📦 {dataset.title} <span className="chip">{dataset.code}</span>
            </span>
          </div>
        </div>
      </div>

      {hasCriteria && (
        <div className="modal-section">
          <h4>Your Search Criteria</h4>
          <div className="req-letter-box" style={{ fontSize: "0.8rem" }}>
            {criteria.parameters.length > 0 && <div>Parameters: {criteria.parameters.join(", ")}</div>}
            {(criteria.dateFrom || criteria.dateTo) && (
              <div>
                Date Range: {criteria.dateFrom || "—"} to {criteria.dateTo || "—"}
              </div>
            )}
            {criteria.bounds && (
              <div>
                Spatial Bounds: Lat {criteria.bounds.latMin.toFixed(2)}°–{criteria.bounds.latMax.toFixed(2)}°, Lon{" "}
                {criteria.bounds.lonMin.toFixed(2)}°–{criteria.bounds.lonMax.toFixed(2)}°
              </div>
            )}
          </div>
          <p style={{ fontSize: "0.74rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
            These filters will be sent with your request so administrators can review the exact scope you need.
          </p>
        </div>
      )}

      <div className="modal-section">
        <h4>Research Justification *</h4>
        <div className="form-group">
          <label className="form-label">Explain why you need this data</label>
          <textarea
            className="form-textarea"
            placeholder="Describe your research purpose, intended use, and expected outcomes…"
            value={letter}
            onChange={(e) => setLetter(e.target.value)}
          />
        </div>
      </div>

      <div className="modal-section">
        <h4>Supporting Document (optional)</h4>
        <label className="file-upload-area">
          <input
            type="file"
            accept=".pdf,.doc,.docx"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) {
                setFileName(f.name);
                setFile(f);
              }
            }}
          />
          <div>📎 Click to upload a supporting document</div>
          <div style={{ fontSize: "0.72rem", marginTop: "0.3rem", color: "var(--text-muted)" }}>PDF, DOC, DOCX — max 10MB</div>
          {fileName && <div style={{ marginTop: "0.5rem", fontSize: "0.78rem", color: "var(--accent)" }}>📎 {fileName}</div>}
        </label>
      </div>
    </Modal>
  );
}
