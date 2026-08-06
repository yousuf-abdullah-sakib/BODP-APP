import { apiFetch } from "./client";
import type { VizComputeLimits, VizExportSettings } from "@/lib/types/visualize";

export async function getVizExportSettings(): Promise<VizExportSettings> {
  return apiFetch<VizExportSettings>("/admin/settings/visualization-exports");
}

export async function updateVizExportSettings(
  data: Partial<VizExportSettings>
): Promise<VizExportSettings> {
  return apiFetch<VizExportSettings>("/admin/settings/visualization-exports", {
    method: "PATCH",
    body: data,
  });
}

export async function getVizComputeLimits(): Promise<VizComputeLimits> {
  return apiFetch<VizComputeLimits>("/admin/settings/visualization-limits");
}

// Date-range fields are already typed `number | null` on VizComputeLimits,
// so Partial<VizComputeLimits> lets a caller either omit a field (leave
// unchanged) or pass explicit null (clear that module's limit back to
// unlimited) — JSON.stringify preserves null (unlike undefined, which it
// drops), matching the backend's exclude_unset PATCH semantics.
export async function updateVizComputeLimits(
  data: Partial<VizComputeLimits>
): Promise<VizComputeLimits> {
  return apiFetch<VizComputeLimits>("/admin/settings/visualization-limits", {
    method: "PATCH",
    body: data,
  });
}
