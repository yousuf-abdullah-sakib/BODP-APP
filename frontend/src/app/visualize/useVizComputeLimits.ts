"use client";

import { useEffect, useState } from "react";
import { getVizComputeLimits } from "@/lib/api/admin-settings";
import type { VizComputeLimits } from "@/lib/types/visualize";

// Generous placeholder defaults while the real config loads, so nothing
// false-warns/false-rejects client-side before the actual admin-configured
// values arrive — the backend is always the authoritative enforcement
// point regardless of what this hook returns.
const DEFAULT_LIMITS: VizComputeLimits = {
  viz_max_grid_resolution: 100,
  viz_max_aoi_km2: 500,
  viz_max_date_range_days_spatial: 3650,
  viz_max_date_range_days_timeseries: null,
  viz_max_date_range_days_comparison: null,
  viz_max_date_range_days_statistics: null,
};

/** Admin-configurable compute limits for /visualize/* (max grid
 * resolution, max AOI area, max date range per module) — used for live
 * client-side warnings before submission. The backend re-enforces every
 * one of these server-side (422 on violation); this hook only drives UI
 * feedback, never blocks a request itself. */
export function useVizComputeLimits(): VizComputeLimits {
  const [limits, setLimits] = useState<VizComputeLimits>(DEFAULT_LIMITS);

  useEffect(() => {
    let cancelled = false;
    getVizComputeLimits()
      .then((data) => {
        if (!cancelled) setLimits(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return limits;
}
