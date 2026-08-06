"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { getVizComputeLimits, updateVizComputeLimits } from "@/lib/api/admin-settings";
import { ApiError } from "@/lib/api/client";
import type { VizComputeLimits } from "@/lib/types/visualize";

const DEFAULT_LIMITS: VizComputeLimits = {
  viz_max_grid_resolution: 100,
  viz_max_aoi_km2: 500,
  viz_max_date_range_days_spatial: 3650,
  viz_max_date_range_days_timeseries: null,
  viz_max_date_range_days_comparison: null,
  viz_max_date_range_days_statistics: null,
};

const DAYS_PER_YEAR = 365;

// A single admin-editable limit: an "Unlimited" checkbox that stores/
// clears null (an admin never types "null" or leaves the field blank to
// mean unlimited — the checkbox is the only way to reach that state), and
// a numeric input that's disabled while Unlimited is checked. The field
// remembers its last numeric value so unchecking Unlimited restores it
// instead of resetting to some arbitrary default.
function LimitRow({
  label,
  desc,
  value,
  unit,
  min,
  max,
  step,
  fallback,
  onChange,
}: {
  label: string;
  desc: string;
  value: number | null;
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
  fallback: number;
  onChange: (value: number | null) => void;
}) {
  // Tracks the last known numeric value purely so unchecking "Unlimited"
  // restores it instead of resetting to an arbitrary default.
  const [lastNumeric, setLastNumeric] = useState(value ?? fallback);
  const unlimited = value === null;

  useEffect(() => {
    if (value !== null) setLastNumeric(value);
  }, [value]);

  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginBottom: "0.4rem" }}>{desc}</div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.7rem" }}>
        <input
          className="form-input"
          type="number"
          min={min}
          max={max}
          step={step}
          style={{ maxWidth: 140, opacity: unlimited ? 0.5 : 1 }}
          value={unlimited ? "" : value}
          disabled={unlimited}
          placeholder={unlimited ? "Unlimited" : undefined}
          onChange={(e) => {
            if (e.target.value === "") return;
            const n = Number(e.target.value);
            if (!Number.isNaN(n) && n > 0) onChange(n);
          }}
        />
        {unit && !unlimited && <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>{unit}</span>}
        <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.82rem", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={unlimited}
            onChange={(e) => onChange(e.target.checked ? null : lastNumeric)}
          />
          Unlimited
        </label>
      </div>
    </div>
  );
}

export default function VisualizationLimitsSection() {
  const { toast } = useToast();
  const [limits, setLimits] = useState<VizComputeLimits>(DEFAULT_LIMITS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getVizComputeLimits()
      .then((data) => {
        if (!cancelled) setLimits(data);
      })
      .catch(() => {
        if (!cancelled) toast("Failed to load compute limits.", "error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  async function handleSave() {
    setSaving(true);
    try {
      const updated = await updateVizComputeLimits(limits);
      setLimits(updated);
      toast("Compute limits saved.", "success");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save compute limits.", "error");
    } finally {
      setSaving(false);
    }
  }

  function set<K extends keyof VizComputeLimits>(field: K, value: VizComputeLimits[K]) {
    setLimits((prev) => ({ ...prev, [field]: value }));
  }

  // The 4 date-range limits are stored in days but edited in years in
  // this UI (per admin request) — converted only at this boundary.
  function daysToYears(days: number | null): number | null {
    return days === null ? null : Math.round((days / DAYS_PER_YEAR) * 10) / 10;
  }
  function yearsToDays(years: number | null): number | null {
    return years === null ? null : Math.round(years * DAYS_PER_YEAR);
  }

  if (loading) {
    return (
      <>
        <div className="dash-header">
          <div>
            <div className="dash-title">Visualization Compute Limits</div>
            <div className="dash-sub">Guard against oversized requests to the Visualize page.</div>
          </div>
        </div>
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading compute limits…</p>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Visualization Compute Limits</div>
          <div className="dash-sub">
            Guard against oversized requests to the Visualize page — an overly large
            interpolation grid, area of interest, or date range can exhaust server resources.
            Set any limit to Unlimited to disable that check entirely. Requests exceeding an
            active limit are rejected with a clear error; the frontend also warns users before
            they submit.
          </div>
        </div>
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save Limits"}
        </button>
      </div>

      <div className="panel" style={{ marginBottom: "1.2rem" }}>
        <div className="panel-head">
          <span className="panel-title">Spatial Interpolation</span>
        </div>
        <div className="panel-body">
          <LimitRow
            label="Max Grid Resolution"
            desc="Cells per axis (e.g. 100 = up to a 100×100 interpolation grid). The Visualize page&rsquo;s own presets are 20/40/60 (low/medium/high); raise this only if server hardware supports finer grids."
            value={limits.viz_max_grid_resolution}
            min={5}
            max={500}
            fallback={100}
            onChange={(v) => set("viz_max_grid_resolution", v)}
          />
          <LimitRow
            label="Max Area of Interest"
            desc="Maximum bounding-box area for a spatial interpolation request."
            value={limits.viz_max_aoi_km2}
            unit="km²"
            min={1}
            fallback={500}
            onChange={(v) => set("viz_max_aoi_km2", v)}
          />
          <LimitRow
            label="Max Time Range — Spatial Mapping"
            desc="Stricter by default: interpolation is far more expensive per day of data than a plain aggregation query."
            value={daysToYears(limits.viz_max_date_range_days_spatial)}
            unit="years"
            min={0.1}
            step={0.1}
            fallback={10}
            onChange={(v) => set("viz_max_date_range_days_spatial", yearsToDays(v))}
          />
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Max Time Range — Other Modules</span>
        </div>
        <div className="panel-body">
          <LimitRow
            label="Temporal Analysis"
            desc="Time series aggregation queries."
            value={daysToYears(limits.viz_max_date_range_days_timeseries)}
            unit="years"
            min={0.1}
            step={0.1}
            fallback={5}
            onChange={(v) => set("viz_max_date_range_days_timeseries", yearsToDays(v))}
          />
          <LimitRow
            label="Multi-Variable Comparison"
            desc="Paired-series and correlation-matrix queries."
            value={daysToYears(limits.viz_max_date_range_days_comparison)}
            unit="years"
            min={0.1}
            step={0.1}
            fallback={5}
            onChange={(v) => set("viz_max_date_range_days_comparison", yearsToDays(v))}
          />
          <LimitRow
            label="Statistics"
            desc="Box plot, histogram, decomposition, and calendar-heatmap queries."
            value={daysToYears(limits.viz_max_date_range_days_statistics)}
            unit="years"
            min={0.1}
            step={0.1}
            fallback={5}
            onChange={(v) => set("viz_max_date_range_days_statistics", yearsToDays(v))}
          />
        </div>
      </div>
    </>
  );
}
