import { useEffect, useState } from "react";
import { getDatasetRecords, getDatasetStations } from "@/lib/api/catalog";
import type {
  DatasetDetail,
  DatasetRecordPreview,
  QualityBreakdown,
  StationOption,
} from "@/lib/types/catalog";
import type { SpatialBounds } from "@/lib/geo/spatialAoi";

export interface DatasetDetailFilters {
  parameter: string;
  quality: string;
  dateFrom: string;
  dateTo: string;
  bounds: SpatialBounds | null;
  latMin: string;
  latMax: string;
  lonMin: string;
  lonMax: string;
  depthMin: string;
  depthMax: string;
  source: string;
  platform: string;
  station: string;
  format: string;
  processingLevel: string;
}

export const DEFAULT_DETAIL_FILTERS: DatasetDetailFilters = {
  parameter: "",
  quality: "",
  dateFrom: "",
  dateTo: "",
  bounds: null,
  latMin: "",
  latMax: "",
  lonMin: "",
  lonMax: "",
  depthMin: "",
  depthMax: "",
  source: "",
  platform: "",
  station: "",
  format: "",
  processingLevel: "",
};

const EMPTY_BREAKDOWN: QualityBreakdown = { normal: 0, caution: 0, alert: 0 };

/**
 * Backend-driven replacement for the prototype's client-side useMemo filter
 * over an in-memory record array (Master Plan §3 Phase 3 task 5) — filtering
 * (including the spatial bbox) now happens in Postgres via
 * GET /catalog/{id}/records, matching the ST_Intersects-backed endpoint.
 */
export function useDatasetFilters(dataset: DatasetDetail) {
  const [filters, setFilters] = useState<DatasetDetailFilters>(DEFAULT_DETAIL_FILTERS);
  const [stationOptions, setStationOptions] = useState<StationOption[]>([]);
  const [preview, setPreview] = useState<DatasetRecordPreview[]>([]);
  const [matchingCount, setMatchingCount] = useState(0);
  const [datasetTotalCount, setDatasetTotalCount] = useState(0);
  const [qualityBreakdown, setQualityBreakdown] = useState<QualityBreakdown>(EMPTY_BREAKDOWN);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getDatasetStations(dataset.id)
      .then(setStationOptions)
      .catch(() => setStationOptions([]));
  }, [dataset.id]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    const effectiveLatMin = filters.bounds?.latMin ?? (filters.latMin ? Number(filters.latMin) : undefined);
    const effectiveLatMax = filters.bounds?.latMax ?? (filters.latMax ? Number(filters.latMax) : undefined);
    const effectiveLonMin = filters.bounds?.lonMin ?? (filters.lonMin ? Number(filters.lonMin) : undefined);
    const effectiveLonMax = filters.bounds?.lonMax ?? (filters.lonMax ? Number(filters.lonMax) : undefined);

    const timer = setTimeout(() => {
      getDatasetRecords(dataset.id, {
        parameter: filters.parameter || undefined,
        quality: filters.quality || undefined,
        date_from: filters.dateFrom || undefined,
        date_to: filters.dateTo || undefined,
        lat_min: effectiveLatMin,
        lat_max: effectiveLatMax,
        lon_min: effectiveLonMin,
        lon_max: effectiveLonMax,
        depth_min: filters.depthMin ? Number(filters.depthMin) : undefined,
        depth_max: filters.depthMax ? Number(filters.depthMax) : undefined,
        source: filters.source || undefined,
        platform: filters.platform || undefined,
        station: filters.station || undefined,
        format: filters.format || undefined,
        processing_level: filters.processingLevel || undefined,
      })
        .then((res) => {
          if (cancelled) return;
          setPreview(res.preview);
          setMatchingCount(res.matching_count);
          setDatasetTotalCount(res.dataset_total_count);
          setQualityBreakdown(res.quality_breakdown);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 250);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [dataset.id, filters]);

  function update<K extends keyof DatasetDetailFilters>(key: K, value: DatasetDetailFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  function resetFilters() {
    setFilters(DEFAULT_DETAIL_FILTERS);
  }

  const activeCount = Object.entries(filters).filter(([k, v]) => {
    if (k === "bounds") return v !== null;
    return v !== "";
  }).length;

  return {
    preview,
    matchingCount,
    datasetTotalCount,
    qualityBreakdown,
    filters,
    update,
    resetFilters,
    activeCount,
    stationOptions,
    loading,
  };
}
