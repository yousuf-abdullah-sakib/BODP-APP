"use client";

import { useCallback, useEffect, useState } from "react";
import { getVisualizableDatasets, postFilteredStations } from "@/lib/api/visualize";
import type { VisualizableDatasetSummary, VizFilterParams } from "@/lib/types/visualize";
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
export const DEFAULT_VIZ_FILTERS: VizFilters = {
  datasetId: "",
  category: "",
  parameter: "",
  station: "",
  dateFrom: "2022-01-01",
  dateTo: "2024-12-31",
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
  const [filteredStations, setFilteredStations] = useState<StationOption[]>([]);
  const [datasets, setDatasets] = useState<VisualizableDatasetSummary[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(true);

  const update = useCallback(<K extends keyof VizFilters>(key: K, value: VizFilters[K]) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }, []);

  const resetFilters = useCallback(() => setFilters(DEFAULT_VIZ_FILTERS), []);

  useEffect(() => {
    getVisualizableDatasets()
      .then(setDatasets)
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false));
  }, []);

  const selectedDataset = datasets.find((d) => d.id === filters.datasetId) ?? null;
  const availableParams = selectedDataset?.variables ?? [];

  // Selecting a dataset resets the parameter to its first approved
  // variable — the previously-selected dataset's parameter almost never
  // exists on the newly-selected one.
  function selectDataset(datasetId: string) {
    const next = datasets.find((d) => d.id === datasetId);
    setFilters((prev) => ({
      ...prev,
      datasetId,
      parameter: next?.variables[0] ?? "",
    }));
  }

  useEffect(() => {
    let cancelled = false;
    postFilteredStations(toVizFilterParams(filters))
      .then((stations) => {
        if (!cancelled) setFilteredStations(stations);
      })
      .catch(() => {
        if (!cancelled) setFilteredStations([]);
      });
    return () => {
      cancelled = true;
    };
  }, [filters]);

  const activeCount = [
    filters.category,
    filters.station,
    filters.latMin || filters.latMax,
    filters.lonMin || filters.lonMax,
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
  };
}
