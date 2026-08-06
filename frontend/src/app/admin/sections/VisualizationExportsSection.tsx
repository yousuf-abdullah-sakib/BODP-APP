"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { getVizExportSettings, updateVizExportSettings } from "@/lib/api/admin-settings";
import { ApiError } from "@/lib/api/client";
import type { VizExportSettings } from "@/lib/types/visualize";

const DEFAULT_SETTINGS: VizExportSettings = {
  viz_export_temporal_enabled: true,
  viz_export_spatial_enabled: true,
  viz_export_comparison_enabled: true,
  viz_export_statistics_enabled: true,
};

function ToggleRow({
  label,
  desc,
  checked,
  onChange,
}: {
  label: string;
  desc?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="toggle-row">
      <div>
        <div className="toggle-label">{label}</div>
        {desc && <div className="toggle-desc">{desc}</div>}
      </div>
      <label className="toggle">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="toggle-slider" />
      </label>
    </div>
  );
}

export default function VisualizationExportsSection() {
  const { toast } = useToast();
  const [settings, setSettings] = useState<VizExportSettings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getVizExportSettings()
      .then((data) => {
        if (!cancelled) setSettings(data);
      })
      .catch(() => {
        if (!cancelled) toast("Failed to load export settings.", "error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  async function handleToggle(field: keyof VizExportSettings, value: boolean) {
    const previous = settings;
    setSettings((prev) => ({ ...prev, [field]: value }));
    setSaving(true);
    try {
      const updated = await updateVizExportSettings({ [field]: value });
      setSettings(updated);
    } catch (err) {
      setSettings(previous);
      toast(err instanceof ApiError ? err.message : "Failed to update export settings.", "error");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <>
        <div className="dash-header">
          <div>
            <div className="dash-title">Visualization Export Settings</div>
            <div className="dash-sub">Control which Visualize page modules allow chart downloads.</div>
          </div>
        </div>
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading export settings…</p>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Visualization Export Settings</div>
          <div className="dash-sub">
            Control which Visualize page modules allow chart downloads. Disabled modules hide the
            download button on every chart in that module; the spatial interpolation map itself
            never has a download option, by design.
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Chart Export / Download</span>
        </div>
        <div className="panel-body">
          <ToggleRow
            label="Temporal Analysis"
            desc="Time series, seasonal pattern, climatology, rate-of-change, anomaly charts"
            checked={settings.viz_export_temporal_enabled}
            onChange={(v) => handleToggle("viz_export_temporal_enabled", v)}
          />
          <ToggleRow
            label="Spatial Mapping"
            desc="Latitudinal/longitudinal profile charts (the interpolation map is never downloadable)"
            checked={settings.viz_export_spatial_enabled}
            onChange={(v) => handleToggle("viz_export_spatial_enabled", v)}
          />
          <ToggleRow
            label="Multi-Variable Comparison"
            desc="Scatter/regression, dual-axis time series, correlation matrix charts"
            checked={settings.viz_export_comparison_enabled}
            onChange={(v) => handleToggle("viz_export_comparison_enabled", v)}
          />
          <ToggleRow
            label="Statistics"
            desc="Box plot, histogram, annual anomalies, decomposition, calendar heatmap charts"
            checked={settings.viz_export_statistics_enabled}
            onChange={(v) => handleToggle("viz_export_statistics_enabled", v)}
          />
        </div>
      </div>
      {saving && <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginTop: "0.6rem" }}>Saving…</div>}
    </>
  );
}
