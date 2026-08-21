"use client";

import { useCallback, useEffect, useState } from "react";
import { getVisualizableDatasets, postCoverage, postFilteredStations } from "@/lib/api/visualize";
import type { CoverageResponse, VisualizableDatasetSummary, VizFilterParams } from "@/lib/types/visualize";
import type { StationOption } from "@/lib/types/catalog";

export interface VizFilters {
  datasetId: string;
  category: string;
  parameter: string;
  station: string;
  dateFrom: string;
  dateTo: string;
  resolution: "daily" | "monthly" | "seasonal" | "annual";
  latMin: string;
  latMax: string;
  lonMin: string;
  lonMax: string;
  depthMin: string;
  depthMax: string;
}

// No global default parameter (PLAN.md Phase 4) — which parameters exist
// depends entirely on which dataset is selected, so there's no valid
// cross-dataset default anymore. useVizFilters resets `parameter` to the
// selected dataset's first approved variable once one is chosen.
//
// dateFrom/dateTo have no global default either (previously a hardcoded
// "2022-01-01"/"2024-12-31" window, which silently excluded real data
// for any dataset dated outside that range — the confirmed root cause
// of a Zarr-backed dataset's time-series query crashing when the
// default filtered out its entire real 2025-01 extent). selectDataset
// auto-populates both from the chosen dataset's own real temporal
// extent instead (VisualizableDatasetSummary.temporal_start/end).
export const DEFAULT_VIZ_FILTERS: VizFilters = {
  datasetId: "",
  category: "",
  parameter: "",
  station: "",
  dateFrom: "",
  dateTo: "",
  resolution: "monthly",
  latMin: "",
  latMax: "",
  lonMin: "",
  lonMax: "",
  depthMin: "",
  depthMax: "",
};

export function toVizFilterParams(f: VizFilters): VizFilterParams {
  return {
    dataset_id: f.datasetId || null,
    category: f.category || null,
    station: f.station || null,
    date_from: f.dateFrom || null,
    date_to: f.dateTo || null,
    resolution: f.resolution,
    lat_min: f.latMin ? Number(f.latMin) : null,
    lat_max: f.latMax ? Number(f.latMax) : null,
    lon_min: f.lonMin ? Number(f.lonMin) : null,
    lon_max: f.lonMax ? Number(f.lonMax) : null,
    depth_min: f.depthMin ? Number(f.depthMin) : null,
    depth_max: f.depthMax ? Number(f.depthMax) : null,
  };
}

export function useVizFilters() {
  const [filters, setFilters] = useState<VizFilters>(DEFAULT_VIZ_FILTERS);
  // True until the user manually edits dateFrom/dateTo — while true, the
  // date range auto-defaults to the selected dataset's own full temporal
  // extent rather than an arbitrary fixed period (see DEFAULT_VIZ_
  // FILTERS' comment for why the old hardcoded default was a real bug).
  const [dateRangeIsAuto, setDateRangeIsAuto] = useState(true);
  // Same pattern, for latMin/latMax/lonMin/lonMax — auto-defaults to the
  // selected dataset's own real spatial_extent rather than leaving the
  // fields blank. True until the user manually edits one of the four
  // fields, or draws/uploads/clears an AOI (VisualizeClient's
  // handleAoiChange/clearAoi) — all genuine user-applied spatial
  // restrictions, same as a manual edit.
  const [spatialRangeIsAuto, setSpatialRangeIsAuto] = useState(true);
  const [filteredStations, setFilteredStations] = useState<StationOption[]>([]);
  const [datasets, setDatasets] = useState<VisualizableDatasetSummary[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(true);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);

  const update = useCallback(<K extends keyof VizFilters>(key: K, value: VizFilters[K]) => {
    if (key === "dateFrom" || key === "dateTo") setDateRangeIsAuto(false);
    if (key === "latMin" || key === "latMax" || key === "lonMin" || key === "lonMax") setSpatialRangeIsAuto(false);
    setFilters((prev) => ({ ...prev, [key]: value }));
  }, []);

  const resetFilters = useCallback(() => {
    setDateRangeIsAuto(true);
    setSpatialRangeIsAuto(true);
    setFilters(DEFAULT_VIZ_FILTERS);
  }, []);

  useEffect(() => {
    getVisualizableDatasets()
      .then(setDatasets)
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false));
  }, []);

  const selectedDataset = datasets.find((d) => d.id === filters.datasetId) ?? null;
  const availableParams = selectedDataset?.variables ?? [];
  const hasTemporalData = selectedDataset !== null && selectedDataset.temporal_start !== null;

  // Selecting a dataset resets the parameter to its first approved
  // variable — the previously-selected dataset's parameter almost never
  // exists on the newly-selected one. Also resets the date range to the
  // newly-selected dataset's own real extent (or blank, if it has no
  // temporal dimension — never fabricated), discarding any previous
  // dataset's auto-defaulted range, which would otherwise stay stuck at
  // a different dataset's dates.
  function selectDataset(datasetId: string) {
    const next = datasets.find((d) => d.id === datasetId);
    setDateRangeIsAuto(true);
    setSpatialRangeIsAuto(true);
    setFilters((prev) => ({
      ...prev,
      datasetId,
      parameter: next?.variables[0] ?? "",
      dateFrom: next?.temporal_start ?? "",
      dateTo: next?.temporal_end ?? "",
      latMin: next?.spatial_extent ? String(next.spatial_extent.lat_min) : "",
      latMax: next?.spatial_extent ? String(next.spatial_extent.lat_max) : "",
      lonMin: next?.spatial_extent ? String(next.spatial_extent.lon_min) : "",
      lonMax: next?.spatial_extent ? String(next.spatial_extent.lon_max) : "",
    }));
  }

  // Debounced (250ms) the same way the Data Page's useDatasetFilters.ts
  // already debounces its own records fetch — a large gridded/Zarr
  // dataset's coverage computation is genuinely expensive (Performance &
  // Behavior investigation: ~6s for a 15M-cell matching range), and with
  // no debounce every keystroke/filter change fired its own full-cost
  // request. `cancelled` alone only suppressed a STALE state update, it
  // never stopped the backend computation already in flight — debouncing
  // is what actually stops redundant computations from being kicked off
  // in the first place.
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      postFilteredStations(toVizFilterParams(filters))
        .then((stations) => {
          if (!cancelled) setFilteredStations(stations);
        })
        .catch(() => {
          if (!cancelled) setFilteredStations([]);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [filters]);

  // Matching/dataset-total record (or cell) counts for the current
  // filter state (Visualization & Filter Reliability investigation,
  // issue #5) — mirrors the catalog Data page's own matching_count/
  // dataset_total_count display. Meaningless without a dataset
  // selected, so the fetch is skipped entirely until one is (matches
  // CoverageRequest's own "dataset_id required in practice" contract);
  // the no-dataset case is handled by the returned `coverage` value
  // below rather than a synchronous setState call inside the effect.
  // Debounced for the same reason as the stations effect above.
  useEffect(() => {
    if (!filters.datasetId) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      postCoverage({ parameter: filters.parameter || null, ...toVizFilterParams(filters) })
        .then((res) => {
          if (!cancelled) setCoverage(res);
        })
        .catch(() => {
          if (!cancelled) setCoverage(null);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [filters]);

  const activeCount = [
    filters.category,
    filters.station,
    // An untouched auto-defaulted spatial range reflects the selected
    // dataset's own full extent, not a user-applied restriction — don't
    // count it (same treatment as dateFrom/dateTo above).
    !spatialRangeIsAuto && (filters.latMin || filters.latMax),
    !spatialRangeIsAuto && (filters.lonMin || filters.lonMax),
    filters.depthMin || filters.depthMax,
  ].filter(Boolean).length;

  return {
    filters,
    update,
    resetFilters,
    filteredStations,
    activeCount,
    datasets,
    datasetsLoading,
    selectedDataset,
    availableParams,
    selectDataset,
    hasTemporalData,
    dateRangeIsAuto,
    spatialRangeIsAuto,
    // null when no dataset is selected, even if a previous selection's
    // coverage value is still sitting in state -- avoids showing stale
    // coverage numbers from a dataset the user has since cleared.
    coverage: filters.datasetId ? coverage : null,
  };
}
