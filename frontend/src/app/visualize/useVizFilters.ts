"use client";

import { useCallback, useEffect, useState } from "react";
import { postFilteredStations } from "@/lib/api/visualize";
import type { VizFilterParams } from "@/lib/types/visualize";
import type { StationOption } from "@/lib/types/catalog";

export interface VizFilters {
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

export const DEFAULT_VIZ_FILTERS: VizFilters = {
  category: "",
  parameter: "Sea Surface Temp",
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

  const update = useCallback(<K extends keyof VizFilters>(key: K, value: VizFilters[K]) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }, []);

  const resetFilters = useCallback(() => setFilters(DEFAULT_VIZ_FILTERS), []);

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

  return { filters, update, resetFilters, filteredStations, activeCount };
}
