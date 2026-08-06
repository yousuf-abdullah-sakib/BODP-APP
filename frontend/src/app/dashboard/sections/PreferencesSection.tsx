"use client";

import { useEffect, useState } from "react";
import { useTheme } from "@/context/ThemeContext";
import { useToast } from "@/context/ToastContext";
import { getPreferences, updatePreferences } from "@/lib/api/me";
import { ApiError } from "@/lib/api/client";
import type { PreferencesSchema } from "@/lib/types/me";

const DEFAULT_PREFS: PreferencesSchema = {
  notify_request_status: true,
  notify_new_dataset: true,
  notify_weekly_digest: false,
  notify_security_alerts: true,
  notify_newsletter: false,
  date_format: "iso",
  coordinate_format: "dd",
  compact_table_rows: false,
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

export default function PreferencesSection() {
  const { theme, toggleTheme } = useTheme();
  const { toast } = useToast();
  const [prefs, setPrefs] = useState<PreferencesSchema>(DEFAULT_PREFS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getPreferences()
      .then((data) => {
        if (!cancelled) setPrefs(data);
      })
      .catch(() => {
        if (!cancelled) toast("Failed to load your preferences.", "error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  function set<K extends keyof PreferencesSchema>(key: K, value: PreferencesSchema[K]) {
    setPrefs((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      const updated = await updatePreferences(prefs);
      setPrefs(updated);
      toast("Preferences saved.", "success");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to save preferences.", "error");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <>
        <div className="dash-header">
          <div>
            <div className="dash-title">Preferences</div>
            <div className="dash-sub">Customize notifications, display, and locale settings.</div>
          </div>
        </div>
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading your preferences…</p>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Preferences</div>
          <div className="dash-sub">Customize notifications, display, and locale settings.</div>
        </div>
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save Preferences"}
        </button>
      </div>

      <div className="panel" style={{ marginBottom: "1.2rem" }}>
        <div className="panel-head">
          <span className="panel-title">Email Notifications</span>
        </div>
        <div className="panel-body">
          <ToggleRow
            label="Request status updates"
            desc="Get notified when a request is approved or rejected"
            checked={prefs.notify_request_status}
            onChange={(v) => set("notify_request_status", v)}
          />
          <ToggleRow
            label="New dataset announcements"
            checked={prefs.notify_new_dataset}
            onChange={(v) => set("notify_new_dataset", v)}
          />
          <ToggleRow
            label="Weekly digest"
            checked={prefs.notify_weekly_digest}
            onChange={(v) => set("notify_weekly_digest", v)}
          />
          <ToggleRow
            label="Security alerts"
            checked={prefs.notify_security_alerts}
            onChange={(v) => set("notify_security_alerts", v)}
          />
          <ToggleRow
            label="Newsletter"
            checked={prefs.notify_newsletter}
            onChange={(v) => set("notify_newsletter", v)}
          />
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1.2rem" }}>
        <div className="panel-head">
          <span className="panel-title">Display</span>
        </div>
        <div className="panel-body">
          <div className="toggle-row">
            <div>
              <div className="toggle-label">Dark mode</div>
              <div className="toggle-desc">Applies across the whole site</div>
            </div>
            <label className="toggle">
              <input type="checkbox" checked={theme === "dark"} onChange={toggleTheme} />
              <span className="toggle-slider" />
            </label>
          </div>
          <ToggleRow
            label="Compact table rows"
            checked={prefs.compact_table_rows}
            onChange={(v) => set("compact_table_rows", v)}
          />
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Units &amp; Locale</span>
        </div>
        <div className="panel-body">
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Date Format</label>
              <select
                className="form-select"
                value={prefs.date_format}
                onChange={(e) => set("date_format", e.target.value)}
              >
                <option value="iso">YYYY-MM-DD</option>
                <option value="dmy">DD/MM/YYYY</option>
                <option value="mdy">MM/DD/YYYY</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Coordinate Format</label>
              <select
                className="form-select"
                value={prefs.coordinate_format}
                onChange={(e) => set("coordinate_format", e.target.value)}
              >
                <option value="dd">Decimal Degrees</option>
                <option value="dms">Degrees/Minutes/Seconds</option>
              </select>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
