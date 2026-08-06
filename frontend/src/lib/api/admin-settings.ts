import { apiFetch } from "./client";
import type { VizExportSettings } from "@/lib/types/visualize";

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
