import { useEffect, useState } from "react";
import { getDatasetRecords, getDatasetSchema, getDatasetStations } from "@/lib/api/catalog";
import type {
  DatasetDetail,
  DatasetRecordPreview,
  DatasetSchemaFilters,
  QualityBreakdown,
  StationOption,
} from "@/lib/types/catalog";
import type { SpatialBounds } from "@/lib/geo/spatialAoi";

export interface DatasetDetailFilters {
  // Checkbox multi-select — empty array means "all approved parameters"
  // (no filtering), matching the backend's RecordsFilter.parameters.
  parameters: string[];
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
  parameters: [],
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
  // True until the user manually edits dateFrom/dateTo — while true, the
  // date range auto-defaults to the dataset's own full temporal extent
  // (dataset.temporal_start/temporal_end, already computed server-side
  // from stored metadata, never a live scan) rather than an arbitrary
  // fixed period. Tracked separately from filters.dateFrom/dateTo so
  // activeCount doesn't count an untouched auto-default as a
  // user-applied filter.
  const [dateRangeIsAuto, setDateRangeIsAuto] = useState(true);
  // Same pattern, for latMin/latMax/lonMin/lonMax — auto-defaults to the
  // dataset's own real spatial_bbox (already computed server-side from
  // stored geometry, never a live scan) rather than leaving the fields
  // blank. A dataset with no recorded spatial extent leaves all four
  // blank (never fabricated). True until the user edits any of the four
  // fields OR sets an explicit `bounds` (drawn/uploaded AOI) — either one
  // is a genuine user-applied spatial restriction.
  const [spatialRangeIsAuto, setSpatialRangeIsAuto] = useState(true);
  const [stationOptions, setStationOptions] = useState<StationOption[]>([]);
  const [preview, setPreview] = useState<DatasetRecordPreview[]>([]);
  const [matchingCount, setMatchingCount] = useState(0);
  const [datasetTotalCount, setDatasetTotalCount] = useState(0);
  const [qualityBreakdown, setQualityBreakdown] = useState<QualityBreakdown>(EMPTY_BREAKDOWN);
  const [loading, setLoading] = useState(true);
  // null = not yet reviewed (or still loading) — the fallback signal that
  // makes DatasetDetailClient render today's fixed dataset.parameters-
  // driven filters instead of schema-driven ones (PLAN.md Phase 4).
  const [schema, setSchema] = useState<DatasetSchemaFilters | null>(null);

  const hasTemporalData = dataset.temporal_start !== null && dataset.temporal_end !== null;

  // Auto-default the date range and lat/lon range to the dataset's real
  // extent whenever the dataset changes and the user hasn't overridden
  // them yet — a dataset with no temporal/spatial extent recorded leaves
  // the corresponding fields blank (never fabricated) rather than
  // showing a stale/arbitrary range.
  //
  // A plain useEffect, not "adjust state during render" — this page's
  // `dataset` prop comes from a server component's one-time SSR fetch
  // (catalog/[id]/page.tsx), not a client-side prop change on an
  // already-mounted component. The render-time-adjustment pattern only
  // resets state when a *comparison against a previous render* detects a
  // change; on the very first render (server or client-hydration), the
  // comparison's own baseline is initialized from that SAME render, so
  // it can never differ and the block never fires — verified via direct
  // SSR HTML inspection: both date and lat/lon inputs shipped with
  // value="" despite dataset.temporal_start/spatial_bbox being present
  // and correct in the fetched dataset prop the whole time. An effect
  // correctly runs once after mount/hydration regardless of whether the
  // page was server-rendered, which is what this needs.
  const datasetExtentSignature = [
    dataset.id,
    dataset.temporal_start ?? "",
    dataset.temporal_end ?? "",
    dataset.spatial_bbox?.lat_min ?? "",
    dataset.spatial_bbox?.lat_max ?? "",
    dataset.spatial_bbox?.lon_min ?? "",
    dataset.spatial_bbox?.lon_max ?? "",
  ].join("|");
  useEffect(() => {
    setDateRangeIsAuto(true);
    setSpatialRangeIsAuto(true);
    setFilters((prev) => ({
      ...prev,
      dateFrom: dataset.temporal_start ?? "",
      dateTo: dataset.temporal_end ?? "",
      latMin: dataset.spatial_bbox ? String(dataset.spatial_bbox.lat_min) : "",
      latMax: dataset.spatial_bbox ? String(dataset.spatial_bbox.lat_max) : "",
      lonMin: dataset.spatial_bbox ? String(dataset.spatial_bbox.lon_min) : "",
      lonMax: dataset.spatial_bbox ? String(dataset.spatial_bbox.lon_max) : "",
    }));
    // Not using datasetExtentAsFields() here — that function is declared
    // below this effect (after `filters`/`update` in source order) and
    // hoisting a call up into this effect's dependency-free closure would
    // work but reads as though it depends on component state it doesn't;
    // this effect only ever needs `dataset`, already in its dep array via
    // datasetExtentSignature. Kept as an explicit inline literal,
    // identical in shape to datasetExtentAsFields()'s return value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetExtentSignature]);

  useEffect(() => {
    getDatasetStations(dataset.id)
      .then(setStationOptions)
      .catch(() => setStationOptions([]));
    getDatasetSchema(dataset.id)
      .then(setSchema)
      .catch(() => setSchema(null));
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
        parameters: filters.parameters.length > 0 ? filters.parameters : undefined,
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
    if (key === "dateFrom" || key === "dateTo") setDateRangeIsAuto(false);
    if (key === "latMin" || key === "latMax" || key === "lonMin" || key === "lonMax" || key === "bounds") {
      setSpatialRangeIsAuto(false);
    }
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  // The dataset's own real extent as plain filter-field strings — shared
  // by the initial auto-fill effect above, resetFilters(), and
  // resetSpatialToExtent() below, so all three stay byte-for-byte
  // identical instead of three independent copies of the same object
  // literal drifting apart.
  function datasetExtentAsFields(): Pick<DatasetDetailFilters, "latMin" | "latMax" | "lonMin" | "lonMax"> {
    return {
      latMin: dataset.spatial_bbox ? String(dataset.spatial_bbox.lat_min) : "",
      latMax: dataset.spatial_bbox ? String(dataset.spatial_bbox.lat_max) : "",
      lonMin: dataset.spatial_bbox ? String(dataset.spatial_bbox.lon_min) : "",
      lonMax: dataset.spatial_bbox ? String(dataset.spatial_bbox.lon_max) : "",
    };
  }

  function resetFilters() {
    setDateRangeIsAuto(true);
    setSpatialRangeIsAuto(true);
    setFilters({
      ...DEFAULT_DETAIL_FILTERS,
      dateFrom: dataset.temporal_start ?? "",
      dateTo: dataset.temporal_end ?? "",
      ...datasetExtentAsFields(),
    });
  }

  // Data Page AOI/Custom Boundary sync fix — mirrors Visualization's own
  // clearAoi() intent ("clearing removes the active spatial restriction")
  // but reverts to the dataset's real extent instead of blanking the
  // fields, matching this page's own auto-fill convention (Visualization
  // has no such per-dataset default to revert to, since its AOI is drawn
  // freehand with no dataset-extent starting point). Called by
  // DatasetDetailClient's clearSpatial(), alongside its existing
  // update("bounds", null).
  function resetSpatialToExtent() {
    setSpatialRangeIsAuto(true);
    setFilters((prev) => ({ ...prev, ...datasetExtentAsFields() }));
  }

  const activeCount = Object.entries(filters).filter(([k, v]) => {
    if (k === "bounds") return v !== null;
    if (k === "parameters") return Array.isArray(v) && v.length > 0;
    // An untouched auto-defaulted date/spatial range reflects the
    // dataset's own full extent, not a user-applied restriction — don't
    // count it.
    if ((k === "dateFrom" || k === "dateTo") && dateRangeIsAuto) return false;
    if ((k === "latMin" || k === "latMax" || k === "lonMin" || k === "lonMax") && spatialRangeIsAuto) return false;
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
    resetSpatialToExtent,
    activeCount,
    stationOptions,
    loading,
    schema,
    hasTemporalData,
    dateRangeIsAuto,
    spatialRangeIsAuto,
  };
}
