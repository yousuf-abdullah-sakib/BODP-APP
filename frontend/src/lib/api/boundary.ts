import { apiFetch } from "./client";
import type { BoundaryShapefileSummary } from "@/lib/types/visualize";

export async function listBoundaries(): Promise<BoundaryShapefileSummary[]> {
  return apiFetch<BoundaryShapefileSummary[]>("/boundary-shapefiles");
}

export async function getDefaultBoundary(): Promise<BoundaryShapefileSummary | null> {
  return apiFetch<BoundaryShapefileSummary | null>("/boundary-shapefiles/default");
}

export async function createBoundary(
  name: string,
  geojson: GeoJSON.FeatureCollection,
  isDefault: boolean
): Promise<BoundaryShapefileSummary> {
  const form = new FormData();
  form.set("name", name);
  form.set("geojson", JSON.stringify(geojson));
  form.set("is_default", String(isDefault));
  return apiFetch<BoundaryShapefileSummary>("/boundary-shapefiles", { method: "POST", body: form });
}

export async function deleteBoundary(id: string): Promise<void> {
  await apiFetch(`/boundary-shapefiles/${id}`, { method: "DELETE" });
}
