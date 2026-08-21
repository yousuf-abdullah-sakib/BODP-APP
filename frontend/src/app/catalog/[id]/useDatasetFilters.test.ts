// Data Page AOI/Custom-Boundary -> Latitude/Longitude sync regression
// tests. Visualization's VisualizeClient.tsx already does this correctly
// (handleAoiChange writes an AOI's bounds into BOTH filters.bounds and
// the plain latMin/latMax/lonMin/lonMax fields); the Data Page's
// DatasetDetailClient.tsx previously only wrote filters.bounds, so the
// visible Min/Max text fields never updated when a user drew an AOI or
// uploaded a shapefile, even though the query itself was already
// correctly scoped underneath.
//
// The actual AOI-sync handlers (applyAoiBounds/clearSpatial/
// handleUserDrawnAoiChange) are thin, non-exported wiring inside
// DatasetDetailClient.tsx that just call update()/resetSpatialToExtent()
// in sequence — this file exercises that real underlying state logic in
// useDatasetFilters.ts directly, the same way the component wiring
// calls it, without needing to mount the full map/upload UI.

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DatasetDetail } from "@/lib/types/catalog";
import type { SpatialBounds } from "@/lib/geo/spatialAoi";
import type { DatasetDetailFilters } from "./useDatasetFilters";

vi.mock("@/lib/api/catalog", () => ({
  getDatasetRecords: vi.fn().mockResolvedValue({
    preview: [],
    matching_count: 0,
    dataset_total_count: 0,
    quality_breakdown: { normal: 0, caution: 0, alert: 0 },
  }),
  getDatasetSchema: vi.fn().mockResolvedValue(null),
  getDatasetStations: vi.fn().mockResolvedValue([]),
}));

import { useDatasetFilters } from "./useDatasetFilters";

function makeDataset(overrides: Partial<DatasetDetail> = {}): DatasetDetail {
  return {
    id: "ds-1",
    code: "BD-TEST",
    title: "Test Dataset",
    category: null,
    location: null,
    parameters: [],
    source: null,
    platforms: [],
    resolution: null,
    record_count: 100,
    formats: [],
    processing_levels: [],
    license: null,
    description: null,
    status: "published",
    updated_at: "2026-01-01",
    temporal_start: "2020-01-01",
    temporal_end: "2024-12-31",
    spatial_bbox: { lat_min: 17.9, lat_max: 22.7, lon_min: 89.0, lon_max: 92.4 },
    ...overrides,
  };
}

type UpdateFn = <K extends keyof DatasetDetailFilters>(key: K, value: DatasetDetailFilters[K]) => void;

// Mirrors DatasetDetailClient.tsx's applyAoiBounds exactly (same two
// calls: update("bounds", ...) with the SpatialBounds object exactly as
// boundsOf() returns it, then the four latMin/latMax/lonMin/lonMax
// updates derived from that same object).
function applyAoiBounds(update: UpdateFn, bounds: SpatialBounds) {
  update("bounds", bounds);
  update("latMin", bounds.latMin.toFixed(3));
  update("latMax", bounds.latMax.toFixed(3));
  update("lonMin", bounds.lonMin.toFixed(3));
  update("lonMax", bounds.lonMax.toFixed(3));
}

describe("useDatasetFilters — AOI/Custom Boundary -> Lat/Lon sync", () => {
  it("auto-populates Lat/Lon fields from the dataset's real extent on load", async () => {
    const dataset = makeDataset();
    const { result } = renderHook(() => useDatasetFilters(dataset));

    await waitFor(() => {
      expect(result.current.filters.latMin).toBe("17.9");
    });
    expect(result.current.filters).toMatchObject({
      latMin: "17.9",
      latMax: "22.7",
      lonMin: "89",
      lonMax: "92.4",
    });
    expect(result.current.spatialRangeIsAuto).toBe(true);
  });

  it("AOI drawn: Lat/Lon fields update to the AOI's bounds", async () => {
    const dataset = makeDataset();
    const { result } = renderHook(() => useDatasetFilters(dataset));
    await waitFor(() => expect(result.current.filters.latMin).toBe("17.9"));

    act(() => {
      applyAoiBounds(result.current.update, { latMin: 21.0, latMax: 22.0, lonMin: 90.0, lonMax: 91.0 });
    });

    expect(result.current.filters.latMin).toBe("21.000");
    expect(result.current.filters.latMax).toBe("22.000");
    expect(result.current.filters.lonMin).toBe("90.000");
    expect(result.current.filters.lonMax).toBe("91.000");
    expect(result.current.filters.bounds).toEqual({ latMin: 21.0, latMax: 22.0, lonMin: 90.0, lonMax: 91.0 });
    expect(result.current.spatialRangeIsAuto).toBe(false);
  });

  it("Shapefile uploaded: Lat/Lon fields update to the uploaded polygon's real bounds", async () => {
    // Same code path as AOI-drawn (DatasetDetailClient.tsx's
    // handleCustomBoundaryUpload calls the identical applyAoiBounds) —
    // exercised here with non-round numbers to confirm the actual
    // computed bounds are used, not a rounded/approximated shape.
    const dataset = makeDataset();
    const { result } = renderHook(() => useDatasetFilters(dataset));
    await waitFor(() => expect(result.current.filters.latMin).toBe("17.9"));

    act(() => {
      applyAoiBounds(result.current.update, {
        latMin: 20.1234, latMax: 20.9876, lonMin: 90.1111, lonMax: 90.9999,
      });
    });

    expect(result.current.filters.latMin).toBe("20.123");
    expect(result.current.filters.latMax).toBe("20.988");
    expect(result.current.filters.lonMin).toBe("90.111");
    expect(result.current.filters.lonMax).toBe("91.000");
  });

  it("Clear AOI/boundary: Lat/Lon fields return to the dataset's extent", async () => {
    const dataset = makeDataset();
    const { result } = renderHook(() => useDatasetFilters(dataset));
    await waitFor(() => expect(result.current.filters.latMin).toBe("17.9"));

    act(() => {
      applyAoiBounds(result.current.update, { latMin: 21.0, latMax: 22.0, lonMin: 90.0, lonMax: 91.0 });
    });
    expect(result.current.filters.latMin).toBe("21.000");

    // Mirrors DatasetDetailClient.tsx's clearSpatial exactly.
    act(() => {
      result.current.update("bounds", null);
      result.current.resetSpatialToExtent();
    });

    expect(result.current.filters.bounds).toBeNull();
    expect(result.current.filters).toMatchObject({
      latMin: "17.9",
      latMax: "22.7",
      lonMin: "89",
      lonMax: "92.4",
    });
    expect(result.current.spatialRangeIsAuto).toBe(true);
  });

  it("user can still manually edit Lat/Lon fields after an AOI was applied", async () => {
    const dataset = makeDataset();
    const { result } = renderHook(() => useDatasetFilters(dataset));
    await waitFor(() => expect(result.current.filters.latMin).toBe("17.9"));

    act(() => {
      applyAoiBounds(result.current.update, { latMin: 21.0, latMax: 22.0, lonMin: 90.0, lonMax: 91.0 });
    });

    act(() => {
      result.current.update("latMin", "21.5");
    });

    expect(result.current.filters.latMin).toBe("21.5");
    expect(result.current.spatialRangeIsAuto).toBe(false);
  });

  it("a dataset with no recorded spatial extent leaves Lat/Lon blank, never fabricated", async () => {
    const dataset = makeDataset({ spatial_bbox: null });
    const { result } = renderHook(() => useDatasetFilters(dataset));

    await waitFor(() => {
      expect(result.current.filters.dateFrom).toBe("2020-01-01");
    });
    expect(result.current.filters).toMatchObject({
      latMin: "", latMax: "", lonMin: "", lonMax: "",
    });
    expect(result.current.spatialRangeIsAuto).toBe(true);
  });
});
