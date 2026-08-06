"use client";

import { useEffect, useState } from "react";
import { getVizExportSettings } from "@/lib/api/admin-settings";

interface ExportsEnabled {
  temporal: boolean;
  spatial: boolean;
  comparison: boolean;
  statistics: boolean;
}

const DEFAULT_ENABLED: ExportsEnabled = {
  temporal: true,
  spatial: true,
  comparison: true,
  statistics: true,
};

/** Admin-controlled per-module export/download toggles (Master Plan §3
 * Phase 7) — defaults to enabled while loading so charts aren't
 * flicker-hidden on first paint. Backend enforces the authoritative state;
 * this only controls whether the frontend offers the export button. */
export function useVizExportSettings(): ExportsEnabled {
  const [enabled, setEnabled] = useState<ExportsEnabled>(DEFAULT_ENABLED);

  useEffect(() => {
    let cancelled = false;
    getVizExportSettings()
      .then((settings) => {
        if (cancelled) return;
        setEnabled({
          temporal: settings.viz_export_temporal_enabled,
          spatial: settings.viz_export_spatial_enabled,
          comparison: settings.viz_export_comparison_enabled,
          statistics: settings.viz_export_statistics_enabled,
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return enabled;
}
