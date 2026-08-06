"use client";

import { useEffect, useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getCategories } from "@/lib/api/admin-categories";
import { createDataset, updateDataset } from "@/lib/api/admin-datasets";
import type { CategoryPublic } from "@/lib/types/admin-categories";
import type { DatasetAdminDetail, DatasetStatus } from "@/lib/types/admin-datasets";

// No shared taxonomy module exists in the real app yet — these mirror the
// prototype's bodp-frontend/src/lib/mock-data/taxonomy.ts static lists.
const PLATFORMS = [
  "Fixed Buoy",
  "Research Vessel",
  "Coastal Station",
  "Satellite",
  "Autonomous Glider",
  "Model / Reanalysis",
];

const RESOLUTIONS = ["Hourly", "Daily", "Weekly", "Monthly", "Seasonal"];

const PROCESSING_LEVELS = ["L0 — Raw", "L1 — Calibrated", "L2 — Derived", "L3 — Gridded/Modeled"];

const SOURCES = ["BORI Station", "Copernicus", "ERA5", "Coastal Survey", "Model Data"];

interface DatasetModalProps {
  dataset: DatasetAdminDetail | null;
  onClose: () => void;
  onSaved: () => void;
}

export default function DatasetModal({ dataset, onClose, onSaved }: DatasetModalProps) {
  const { toast } = useToast();
  const isNew = !dataset;
  const [categories, setCategories] = useState<CategoryPublic[]>([]);
  const [title, setTitle] = useState(dataset?.title ?? "");
  const [categoryId, setCategoryId] = useState(dataset?.category_id ?? "");
  const [location, setLocation] = useState(dataset?.location ?? "");
  const [paramsText, setParamsText] = useState(dataset?.parameters.join(", ") ?? "");
  const [source, setSource] = useState(dataset?.source ?? SOURCES[0]);
  const [license, setLicense] = useState(dataset?.license ?? "");
  const [description, setDescription] = useState(dataset?.description ?? "");
  const [status, setStatus] = useState<DatasetStatus>((dataset?.status as DatasetStatus) ?? "draft");
  const [platforms, setPlatforms] = useState<string[]>(dataset?.platforms ?? []);
  const [resolution, setResolution] = useState(dataset?.resolution ?? RESOLUTIONS[1]);
  const [processingLevels, setProcessingLevels] = useState<string[]>(dataset?.processing_levels ?? []);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getCategories()
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);

  function togglePlatform(p: string) {
    setPlatforms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  }

  function toggleProcessingLevel(p: string) {
    setProcessingLevels((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  }

  async function save() {
    if (!title.trim()) {
      toast("Dataset title is required.", "error");
      return;
    }
    setSaving(true);
    try {
      const parameters = paramsText
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean);
      if (isNew) {
        await createDataset({
          title: title.trim(),
          description: description || null,
          category_id: categoryId || null,
          location: location || null,
          source: source || null,
          platforms,
          parameters,
          resolution: resolution || null,
          license: license || null,
          processing_levels: processingLevels,
          status,
        });
      } else {
        // Status transitions are handled by the dedicated publish/archive
        // actions in AdminDatasetsSection, not by this free-form field —
        // intentionally omitted from the edit payload.
        await updateDataset(dataset.id, {
          title: title.trim(),
          description: description || null,
          category_id: categoryId || null,
          location: location || null,
          source: source || null,
          platforms,
          parameters,
          resolution: resolution || null,
          license: license || null,
          processing_levels: processingLevels,
        });
      }
      toast(isNew ? "Dataset created." : "Dataset updated.", "success");
      onSaved();
      onClose();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save dataset.", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={isNew ? "Add Dataset" : "Edit Dataset"}
      onClose={onClose}
      large
      footer={
        <>
          <button className="btn-cancel" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn-submit" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save Dataset"}
          </button>
        </>
      }
    >
      <div className="form-group">
        <label className="form-label">Dataset Title *</label>
        <input
          className="form-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Cox's Bazar Microplastics Q3 2024"
        />
      </div>
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">Category</label>
          <select className="form-select" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">Uncategorized</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Location / Station</label>
          <input
            className="form-input"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="Cox's Bazar"
          />
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">Parameters</label>
          <input
            className="form-input"
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
            placeholder="Salinity, pH, Turbidity"
          />
        </div>
        <div className="form-group">
          <label className="form-label">Source</label>
          <select className="form-select" value={source} onChange={(e) => setSource(e.target.value)}>
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="form-row">
        {!isNew && (
          <div className="form-group">
            <label className="form-label">Record Count</label>
            <input className="form-input" value={dataset.record_count.toLocaleString()} disabled />
          </div>
        )}
        <div className="form-group">
          <label className="form-label">License</label>
          <input className="form-input" value={license} onChange={(e) => setLicense(e.target.value)} />
        </div>
        <div className="form-group">
          <label className="form-label">Data Resolution</label>
          <select className="form-select" value={resolution} onChange={(e) => setResolution(e.target.value)}>
            {RESOLUTIONS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">Observation Platforms</label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
          {PLATFORMS.map((p) => (
            <label key={p} className={`chip-checkbox${platforms.includes(p) ? " active" : ""}`}>
              <input type="checkbox" checked={platforms.includes(p)} onChange={() => togglePlatform(p)} />
              {p}
            </label>
          ))}
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">Processing Levels Available</label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
          {PROCESSING_LEVELS.map((p) => (
            <label key={p} className={`chip-checkbox${processingLevels.includes(p) ? " active" : ""}`}>
              <input type="checkbox" checked={processingLevels.includes(p)} onChange={() => toggleProcessingLevel(p)} />
              {p}
            </label>
          ))}
        </div>
      </div>

      <div className="form-group">
        <label className="form-label">Description</label>
        <textarea className="form-textarea" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>

      {isNew && (
        <div className="form-group">
          <label className="form-label">Status</label>
          <select className="form-select" value={status} onChange={(e) => setStatus(e.target.value as DatasetStatus)}>
            <option value="draft">Draft</option>
            <option value="published">Published</option>
            <option value="archived">Archived</option>
          </select>
        </div>
      )}
    </Modal>
  );
}
