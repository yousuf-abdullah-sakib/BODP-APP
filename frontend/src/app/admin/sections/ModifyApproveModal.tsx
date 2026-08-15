"use client";

import { useEffect, useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getCatalogTaxonomy, getDatasetSchema } from "@/lib/api/catalog";
import { modifyRequest } from "@/lib/api/admin-requests";
import type { RequestDetail } from "@/lib/types/requests";
import type { SearchCriteria } from "@/lib/types/requests";

interface ModifyApproveModalProps {
  request: RequestDetail;
  onClose: () => void;
  onConfirm: (note: string, criteria: SearchCriteria | undefined) => void;
  /** Called after a successful Save Changes so the caller (AdminRequestsSection)
   * can refetch the request list — Save Changes does not close this modal or
   * approve/reject, an admin may keep reviewing afterward. */
  onSaved?: () => void;
}

function CriteriaSummary({ criteria }: { criteria: SearchCriteria }) {
  const hasAny =
    criteria.category ||
    (criteria.parameters && criteria.parameters.length > 0) ||
    criteria.source ||
    criteria.date_from ||
    criteria.date_to ||
    criteria.bounds;
  if (!hasAny) {
    return <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>No filters were set.</p>;
  }
  return (
    <div className="req-letter-box" style={{ fontSize: "0.78rem" }}>
      {criteria.category && <div>Category: {criteria.category}</div>}
      {criteria.parameters && criteria.parameters.length > 0 && (
        <div>Parameters: {criteria.parameters.join(", ")}</div>
      )}
      {criteria.source && <div>Source: {criteria.source}</div>}
      {(criteria.date_from || criteria.date_to) && (
        <div>
          Date Range: {criteria.date_from || "—"} to {criteria.date_to || "—"}
        </div>
      )}
      {criteria.bounds && (
        <div>
          Spatial Bounds: Lat {criteria.bounds.lat_min.toFixed(2)}°–{criteria.bounds.lat_max.toFixed(2)}° &nbsp;|&nbsp;
          Lon {criteria.bounds.lon_min.toFixed(2)}°–{criteria.bounds.lon_max.toFixed(2)}°
        </div>
      )}
    </div>
  );
}

export default function ModifyApproveModal({ request, onClose, onConfirm, onSaved }: ModifyApproveModalProps) {
  const { toast } = useToast();
  const [note, setNote] = useState("");
  // Editable draft starts from the admin's previously-saved modification
  // if one exists, otherwise from the original — either way, edits here
  // are saved separately and NEVER write back into request.search_criteria.
  const [criteria, setCriteria] = useState<SearchCriteria>(
    request.admin_modified_search_criteria ?? request.search_criteria ?? {}
  );
  const [categories, setCategories] = useState<string[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [availableParameters, setAvailableParameters] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

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

  useEffect(() => {
    // Parameter options are dataset-specific and come from the approved
    // schema (Phase 3/4 DatasetVariable review) — a dataset that hasn't
    // been reviewed yet has no approved parameter list, so the checkbox
    // group is simply empty rather than falling back to something
    // unapproved.
    getDatasetSchema(request.dataset.id)
      .then((schema) => {
        const names = schema
          ? schema.variables.filter((v) => !v.roles.includes("dimension") && v.roles.length > 0).map((v) => v.name)
          : [];
        setAvailableParameters(names);
      })
      .catch(() => setAvailableParameters([]));
  }, [request.dataset.id]);

  function updateCriteria<K extends keyof SearchCriteria>(key: K, value: SearchCriteria[K]) {
    setCriteria((prev) => ({ ...prev, [key]: value }));
  }

  function toggleParameter(name: string) {
    const current = criteria.parameters ?? [];
    updateCriteria(
      "parameters",
      current.includes(name) ? current.filter((p) => p !== name) : [...current, name]
    );
  }

  const hasOriginalCriteria = !!request.search_criteria;
  const hasSavedModification = !!request.admin_modified_search_criteria;

  async function handleSaveChanges() {
    setSaving(true);
    try {
      await modifyRequest(request.id, criteria);
      toast("Filter configuration saved. Approve when ready.", "success");
      onSaved?.();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save changes.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title="Review & Modify Request"
      onClose={onClose}
      large
      footer={
        <>
          <button className="btn-ghost-sm" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-ghost-sm" onClick={handleSaveChanges} disabled={saving}>
            {saving ? "Saving…" : "💾 Save Changes"}
          </button>
          <button
            className="btn-success-sm"
            onClick={() => onConfirm(note.trim(), hasOriginalCriteria || hasSavedModification ? criteria : undefined)}
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

      <div className="mini-label">Original User Request</div>
      <p style={{ fontSize: "0.74rem", color: "var(--text-muted)", marginBottom: "0.5rem" }}>
        Exactly what the researcher submitted — read-only, never changed by admin review.
      </p>
      {request.search_criteria ? (
        <CriteriaSummary criteria={request.search_criteria} />
      ) : (
        <p style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>No filters were set by the requester.</p>
      )}

      {hasSavedModification && (
        <>
          <div className="mini-label" style={{ marginTop: "1rem" }}>
            Previously Saved Admin Modification
          </div>
          <CriteriaSummary criteria={request.admin_modified_search_criteria as SearchCriteria} />
        </>
      )}

      <div style={{ height: 1, background: "var(--border)", margin: "1rem 0" }} />

      <div className="mini-label">Edit Filter Configuration</div>
      <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginBottom: "0.8rem", lineHeight: 1.6 }}>
        Adjust before saving/approving. Use <b>Save Changes</b> to persist this configuration for later review
        without deciding yet, or <b>Approve</b> to grant access with the configuration shown here.
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

      <div className="form-group">
        <label className="form-label">
          Parameters {criteria.parameters && criteria.parameters.length > 0 && (
            <span className="chip">{criteria.parameters.length} selected</span>
          )}
        </label>
        {availableParameters.length === 0 ? (
          <p style={{ fontSize: "0.76rem", color: "var(--text-muted)" }}>
            No approved parameters available for this dataset yet.
          </p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
            {availableParameters.map((p) => (
              <label key={p} className={`chip-checkbox${(criteria.parameters ?? []).includes(p) ? " active" : ""}`}>
                <input
                  type="checkbox"
                  checked={(criteria.parameters ?? []).includes(p)}
                  onChange={() => toggleParameter(p)}
                />
                {p}
              </label>
            ))}
          </div>
        )}
        <p style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
          None selected includes all approved parameters.
        </p>
      </div>

      <div className="form-row">
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
