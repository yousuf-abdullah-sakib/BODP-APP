"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { submitRequest } from "@/lib/api/requests";
import { ApiError } from "@/lib/api/client";
import type { DatasetDetail } from "@/lib/types/catalog";
import type { SearchCriteria } from "@/lib/types/requests";
import type { DatasetDetailFilters } from "./useDatasetFilters";

interface DatasetRequestModalProps {
  dataset: DatasetDetail;
  // The full live filter panel state, not a narrowed subset — previously
  // only parameters/dateFrom/dateTo/bounds were carried into the request
  // (quality/depth/source/platform/station/format/processingLevel were
  // silently dropped, confirmed via full-codebase trace), which meant a
  // "snapshot" of the request's filters was never actually complete.
  criteria: DatasetDetailFilters;
  onClose: () => void;
  onSubmitted: () => void;
}

function toSearchCriteria(criteria: DatasetDetailFilters): SearchCriteria | undefined {
  const hasCriteria =
    criteria.parameters.length > 0 ||
    criteria.quality ||
    criteria.dateFrom ||
    criteria.dateTo ||
    criteria.bounds ||
    criteria.depthMin ||
    criteria.depthMax ||
    criteria.source ||
    criteria.platform ||
    criteria.station ||
    criteria.format ||
    criteria.processingLevel;
  if (!hasCriteria) return undefined;

  return {
    parameters: criteria.parameters.length > 0 ? criteria.parameters : undefined,
    quality: criteria.quality || undefined,
    date_from: criteria.dateFrom || undefined,
    date_to: criteria.dateTo || undefined,
    depth_min: criteria.depthMin ? Number(criteria.depthMin) : undefined,
    depth_max: criteria.depthMax ? Number(criteria.depthMax) : undefined,
    source: criteria.source || undefined,
    platform: criteria.platform || undefined,
    station: criteria.station || undefined,
    format: criteria.format || undefined,
    processing_level: criteria.processingLevel || undefined,
    bounds: criteria.bounds
      ? {
          lat_min: criteria.bounds.latMin,
          lat_max: criteria.bounds.latMax,
          lon_min: criteria.bounds.lonMin,
          lon_max: criteria.bounds.lonMax,
        }
      : undefined,
  };
}

export default function DatasetRequestModal({ dataset, criteria, onClose, onSubmitted }: DatasetRequestModalProps) {
  const { toast } = useToast();
  const [letter, setLetter] = useState("");
  const [fileName, setFileName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const searchCriteria = toSearchCriteria(criteria);
  const hasCriteria = searchCriteria !== undefined;

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
        searchCriteria,
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
            {criteria.quality && <div>Quality: {criteria.quality}</div>}
            {(criteria.dateFrom || criteria.dateTo) && (
              <div>
                Date Range: {criteria.dateFrom || "—"} to {criteria.dateTo || "—"}
              </div>
            )}
            {(criteria.depthMin || criteria.depthMax) && (
              <div>
                Depth: {criteria.depthMin || "—"}m to {criteria.depthMax || "—"}m
              </div>
            )}
            {criteria.source && <div>Source: {criteria.source}</div>}
            {criteria.platform && <div>Platform: {criteria.platform}</div>}
            {criteria.station && <div>Station: {criteria.station}</div>}
            {criteria.format && <div>Format: {criteria.format}</div>}
            {criteria.processingLevel && <div>Processing Level: {criteria.processingLevel}</div>}
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
