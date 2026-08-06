"use client";

import { useEffect, useState } from "react";
import Modal from "@/components/ui/Modal";
import { getCatalogTaxonomy } from "@/lib/api/catalog";
import type { RequestDetail } from "@/lib/types/requests";
import type { SearchCriteria } from "@/lib/types/requests";

interface ModifyApproveModalProps {
  request: RequestDetail;
  onClose: () => void;
  onConfirm: (note: string, criteria: SearchCriteria | undefined) => void;
}

export default function ModifyApproveModal({ request, onClose, onConfirm }: ModifyApproveModalProps) {
  const [note, setNote] = useState("");
  const [criteria, setCriteria] = useState<SearchCriteria>(request.search_criteria ?? {});
  const [categories, setCategories] = useState<string[]>([]);
  const [sources, setSources] = useState<string[]>([]);

  useEffect(() => {
    getCatalogTaxonomy()
      .then((t) => {
        setCategories(t.categories);
        setSources(t.sources);
      })
      .catch(() => {
        setCategories([]);
        setSources([]);
      });
  }, []);

  function updateCriteria<K extends keyof SearchCriteria>(key: K, value: SearchCriteria[K]) {
    setCriteria((prev) => ({ ...prev, [key]: value }));
  }

  const hasCriteria = !!request.search_criteria;

  return (
    <Modal
      title="Modify Request Before Approval"
      onClose={onClose}
      large
      footer={
        <>
          <button className="btn-ghost-sm" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-success-sm"
            onClick={() => onConfirm(note.trim(), hasCriteria ? criteria : undefined)}
          >
            ✓ Approve
          </button>
        </>
      }
    >
      <div className="mini-label">Requested Dataset</div>
      <div className="req-dataset-list" style={{ marginBottom: "1rem" }}>
        <div className="req-dataset-row">
          <span>📦 {request.dataset.title}</span>
          <span className="chip">{request.dataset.code}</span>
        </div>
      </div>

      {hasCriteria && (
        <>
          <div className="mini-label">Original Search Criteria</div>
          <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginBottom: "0.8rem", lineHeight: 1.6 }}>
            Filters the researcher used when they arrived at this request. Adjust before approving if the scope
            should be narrowed.
          </p>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Category</label>
              <select
                className="form-select"
                value={criteria.category ?? ""}
                onChange={(e) => updateCriteria("category", e.target.value || undefined)}
              >
                <option value="">Any</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Source</label>
              <select
                className="form-select"
                value={criteria.source ?? ""}
                onChange={(e) => updateCriteria("source", e.target.value || undefined)}
              >
                <option value="">Any</option>
                {sources.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Parameter</label>
              <input
                className="form-input"
                value={criteria.parameter ?? ""}
                onChange={(e) => updateCriteria("parameter", e.target.value || undefined)}
                placeholder="e.g. Dissolved Oxygen"
              />
            </div>
            <div className="form-group">
              <label className="form-label">Date From</label>
              <input
                type="date"
                className="form-input"
                value={criteria.date_from ?? ""}
                onChange={(e) => updateCriteria("date_from", e.target.value || undefined)}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Date To</label>
              <input
                type="date"
                className="form-input"
                value={criteria.date_to ?? ""}
                onChange={(e) => updateCriteria("date_to", e.target.value || undefined)}
              />
            </div>
          </div>
          {criteria.bounds && (
            <div className="form-group" style={{ marginBottom: "1.1rem" }}>
              <label className="form-label">Spatial Bounds</label>
              <div className="req-letter-box" style={{ fontSize: "0.78rem" }}>
                Lat {criteria.bounds.lat_min.toFixed(2)}° – {criteria.bounds.lat_max.toFixed(2)}° &nbsp;|&nbsp; Lon{" "}
                {criteria.bounds.lon_min.toFixed(2)}° – {criteria.bounds.lon_max.toFixed(2)}°
              </div>
            </div>
          )}
          <div style={{ height: 1, background: "var(--border)", margin: "0.4rem 0 1.1rem" }} />
        </>
      )}

      <div className="form-group">
        <label className="form-label">Internal note (optional)</label>
        <textarea
          className="form-textarea"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. Approved with narrowed date range pending QC review."
        />
      </div>
    </Modal>
  );
}
