"use client";

import { useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useVizFilters } from "./useVizFilters";
import { useVizComputeLimits } from "./useVizComputeLimits";
import TemporalModule from "./modules/TemporalModule";
import { useSpatialMapping } from "./modules/SpatialMappingModule";
import ComparisonModule from "./modules/ComparisonModule";
import StatisticsModule from "./modules/StatisticsModule";
import ProfilesModule from "./modules/ProfilesModule";
import { boundsOf, type SpatialAOI } from "@/lib/geo/spatialAoi";
import { bboxAreaKm2 } from "@/lib/geo/bboxArea";
import { parseShapefile, hasUsableGeometry } from "@/lib/geo/shapefileUpload";
import { useToast } from "@/context/ToastContext";

const SpatialFilterMap = dynamic(() => import("@/components/map/SpatialFilterMap"), {
  ssr: false,
  loading: () => (
    <div style={{ height: 200, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: "0.75rem" }}>
      Loading map…
    </div>
  ),
});

const MODULES = [
  { key: "temporal", icon: "📈", label: "Temporal Analysis" },
  { key: "spatial", icon: "🗺️", label: "Spatial Mapping" },
  { key: "comparison", icon: "⚖️", label: "Multi-Variable" },
  { key: "statistics", icon: "📐", label: "Statistics" },
  { key: "profiles", icon: "🌊", label: "Oceanographic Profiles" },
] as const;

type ModuleKey = (typeof MODULES)[number]["key"];

function FilterGroup({ title, icon, children }: { title: string; icon: string; children: React.ReactNode }) {
  return (
    <div className="ctrl-section">
      <div className="ctrl-heading">
        <span>
          {icon} {title}
        </span>
      </div>
      {children}
    </div>
  );
}

export default function VisualizeClient() {
  const [module, setModule] = useState<ModuleKey>("temporal");
  const {
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
    coverage,
  } = useVizFilters();
  const limits = useVizComputeLimits();
  const [showTrend, setShowTrend] = useState(true);
  const [showMA, setShowMA] = useState(true);
  const [aoiClearSignal, setAoiClearSignal] = useState(0);
  const [aoi, setAoi] = useState<SpatialAOI | null>(null);

  // Custom Boundary upload — same parseShapefile logic/component the
  // Spatial Mapping module's own Custom Boundary control already uses,
  // now wired into the SAME AOI pipeline a hand-drawn shape uses: the
  // uploaded polygon becomes `aoi` via handleAoiChange (below), which
  // already drives both the lat/lon filter fields and useSpatialMapping's
  // own bounds — so no separate filter logic is needed, it rides the
  // existing one. uploadedBoundaryAoi is kept separate from `aoi` purely
  // so SpatialFilterMap knows to draw THIS shape specifically (a hand-
  // drawn shape already draws itself); it's cleared whenever the user
  // draws something new or hits Clear, so the boundary stays active
  // until the user changes or clears it, never silently stale.
  const { toast } = useToast();
  const [customBoundaryName, setCustomBoundaryName] = useState("");
  const [uploadedBoundaryAoi, setUploadedBoundaryAoi] = useState<SpatialAOI | null>(null);
  const boundaryFileInputRef = useRef<HTMLInputElement | null>(null);

  async function handleCustomBoundaryUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const geo = await parseShapefile(f);
      if (!hasUsableGeometry(geo)) {
        toast("That file has no usable polygon boundary.", "error");
      } else {
        // Full parsed geometry, not a reduced single ring -- every
        // feature/Polygon/MultiPolygon part/hole renders and filters
        // exactly as uploaded (see SpatialAOI's "geometry" kind).
        const nextAoi: SpatialAOI = { kind: "geometry", geojson: geo };
        setCustomBoundaryName(f.name);
        setUploadedBoundaryAoi(nextAoi);
        handleAoiChange(nextAoi);
        toast(`Loaded boundary from ${f.name} — now applied as the active spatial filter.`, "success");
      }
    } catch {
      toast("Could not read that file as a shapefile. Upload a .zip (shp+dbf+prj) bundle.", "error");
    }
    e.target.value = "";
  }

  function clearCustomBoundary() {
    clearAoi();
  }

  // Live AOI area feedback — only meaningful for the Spatial module, since
  // AOI area is a spatial-only limit. Warn-only: the AOI is never silently
  // cropped or modified here, actual enforcement is the backend 422.
  const aoiAreaKm2 = useMemo(() => {
    if (filters.latMin === "" || filters.latMax === "" || filters.lonMin === "" || filters.lonMax === "") {
      return null;
    }
    return bboxAreaKm2({
      latMin: Number(filters.latMin),
      latMax: Number(filters.latMax),
      lonMin: Number(filters.lonMin),
      lonMax: Number(filters.lonMax),
    });
  }, [filters.latMin, filters.latMax, filters.lonMin, filters.lonMax]);
  const aoiOverLimit =
    module === "spatial" &&
    aoiAreaKm2 !== null &&
    limits.viz_max_aoi_km2 !== null &&
    aoiAreaKm2 > limits.viz_max_aoi_km2;

  // Live date-range feedback, checked against whichever module is active.
  const dateRangeDays = useMemo(() => {
    if (!filters.dateFrom || !filters.dateTo) return null;
    const from = new Date(filters.dateFrom).getTime();
    const to = new Date(filters.dateTo).getTime();
    if (Number.isNaN(from) || Number.isNaN(to)) return null;
    return Math.round((to - from) / (1000 * 60 * 60 * 24));
  }, [filters.dateFrom, filters.dateTo]);
  const maxDateRangeDaysForModule: number | null =
    module === "spatial"
      ? limits.viz_max_date_range_days_spatial
      : module === "temporal"
        ? limits.viz_max_date_range_days_timeseries
        : module === "comparison"
          ? limits.viz_max_date_range_days_comparison
          : limits.viz_max_date_range_days_statistics;
  const dateRangeOverLimit =
    dateRangeDays !== null && maxDateRangeDaysForModule !== null && dateRangeDays > maxDateRangeDaysForModule;

  function handleAoiChange(next: SpatialAOI | null) {
    setAoi(next);
    if (!next) return;
    const bounds = boundsOf(next);
    update("latMin", bounds.latMin.toFixed(3));
    update("latMax", bounds.latMax.toFixed(3));
    update("lonMin", bounds.lonMin.toFixed(3));
    update("lonMax", bounds.lonMax.toFixed(3));
  }

  // Hand-drawing a new shape abandons any active uploaded boundary —
  // "the user changes it" — so this (not handleAoiChange itself, which
  // the upload path also reuses) is what SpatialFilterMap's onAoiChange
  // actually gets wired to.
  function handleUserDrawnAoiChange(next: SpatialAOI | null) {
    setCustomBoundaryName("");
    setUploadedBoundaryAoi(null);
    handleAoiChange(next);
  }

  function clearAoi() {
    setAoiClearSignal((s) => s + 1);
    setAoi(null);
    setCustomBoundaryName("");
    setUploadedBoundaryAoi(null);
    update("latMin", "");
    update("latMax", "");
    update("lonMin", "");
    update("lonMax", "");
  }

  const spatial = useSpatialMapping({ parameter: filters.parameter, aoi, filters, selectedDataset });

  return (
    <>
      <div className="viz-hero">
        <div className="viz-hero-inner">
          <div>
            <div className="section-tag">📊 Scientific Visualization</div>
            <h1>
              Interactive <em>Ocean Data</em> Explorer
            </h1>
            <p>
              Time-series analysis, GIS-based spatial mapping with interpolation, and multi-variable comparison for
              Bay of Bengal oceanographic data. Select a module, configure filters, and explore.
            </p>
          </div>
        </div>
      </div>

      <div className="module-tabs">
        {MODULES.map((m) => (
          <button
            key={m.key}
            className={`module-tab${module === m.key ? " active" : ""}`}
            onClick={() => setModule(m.key)}
          >
            <span className="tab-icon">{m.icon}</span> {m.label}
          </button>
        ))}
      </div>

      <div className={`viz-shell${module === "spatial" ? " viz-shell-3col" : ""}`}>
        <aside className="ctrl-panel">
          <div className="ctrl-section" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div className="ctrl-heading" style={{ border: "none", margin: 0, padding: 0 }}>
              🧰 Filters {activeCount > 0 && <span className="chip">{activeCount} active</span>}
            </div>
            <button className="btn-reset-ctrl" style={{ width: "auto", padding: "0.3rem 0.7rem", marginTop: 0 }} onClick={() => { resetFilters(); setAoi(null); setAoiClearSignal((s) => s + 1); setCustomBoundaryName(""); setUploadedBoundaryAoi(null); }}>
              Reset
            </button>
          </div>

          <FilterGroup title="Dataset" icon="📦">
            <div className="ctrl-group">
              <label className="ctrl-label">Dataset</label>
              <select
                className="ctrl-select"
                value={filters.datasetId}
                onChange={(e) => selectDataset(e.target.value)}
              >
                <option value="">
                  {datasetsLoading ? "Loading datasets…" : "Select a dataset…"}
                </option>
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title} ({d.code})
                  </option>
                ))}
              </select>
              {!datasetsLoading && datasets.length === 0 && (
                <p className="gis-caption" style={{ marginBottom: 0 }}>
                  No datasets have an approved schema for visualization yet.
                </p>
              )}
            </div>
            <div className="ctrl-group">
              <label className="ctrl-label">Parameter / Variable</label>
              <select
                className="ctrl-select"
                value={filters.parameter}
                onChange={(e) => update("parameter", e.target.value)}
                disabled={!selectedDataset}
              >
                {availableParams.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div className="ctrl-group">
              <label className="ctrl-label">Station</label>
              <select className="ctrl-select" value={filters.station} onChange={(e) => update("station", e.target.value)}>
                <option value="">All Stations</option>
                {filteredStations.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            {coverage && (
              <div className="map-filter-info">
                <b>{coverage.matching_count.toLocaleString()}</b> of{" "}
                {coverage.dataset_total_count.toLocaleString()} {coverage.unit} match the current
                filters ({coverage.coverage_percent.toFixed(1)}% coverage)
                {coverage.notes.length > 0 && (
                  <ul style={{ margin: "0.35rem 0 0", paddingLeft: "1.1rem", color: "var(--yellow)" }}>
                    {coverage.notes.map((note, i) => (
                      <li key={i}>{note}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </FilterGroup>

          <FilterGroup title="Area of Interest (AOI)" icon="🌍">
            <div className="map-filter-box">
              <div className="map-filter-head">
                <span>Draw a box or polygon to define your AOI</span>
                <button className="btn-clear-spatial" onClick={clearAoi}>
                  Clear
                </button>
              </div>
              {/* No `stations` prop here (unlike the catalog detail page's
                  own SpatialFilterMap usage) -- this AOI-drawing map is
                  shared across all 4 Visualize modules with no single
                  dataset+parameter-scoped station list to show, and
                  `filteredStations` is a bbox/depth-only, dataset-
                  agnostic list that used to render as if it were
                  relevant to the current selection. The Spatial Mapping
                  module's own map shows the selected dataset+parameter's
                  actual data locations; this map's only job is drawing
                  the AOI shape. */}
              <SpatialFilterMap
                onAoiChange={handleUserDrawnAoiChange}
                clearSignal={aoiClearSignal}
                enablePolygon
                externalAoi={uploadedBoundaryAoi}
              />
              <div className="map-filter-info">
                {aoi ? (
                  aoi.kind === "geometry" ? (
                    <>
                      <b>Selected:</b> Custom boundary ({aoi.geojson.features.length} feature
                      {aoi.geojson.features.length === 1 ? "" : "s"})
                    </>
                  ) : aoi.kind === "polygon" ? (
                    <>
                      <b>Selected:</b> Polygon area ({aoi.ring.length} vertices)
                    </>
                  ) : (
                    <>
                      <b>Selected:</b> Lat {aoi.bounds.latMin.toFixed(2)}–{aoi.bounds.latMax.toFixed(2)}, Lon {aoi.bounds.lonMin.toFixed(2)}–{aoi.bounds.lonMax.toFixed(2)}
                    </>
                  )
                ) : (
                  "No area selected — showing all locations"
                )}
              </div>
              {aoiAreaKm2 !== null && (
                <div
                  className="map-filter-info"
                  style={aoiOverLimit ? { color: "var(--yellow)" } : undefined}
                >
                  {aoiOverLimit ? "⚠ " : ""}Approx. area: ~{aoiAreaKm2.toFixed(0)} km²
                  {module === "spatial" && limits.viz_max_aoi_km2 !== null && ` (max ${limits.viz_max_aoi_km2.toFixed(0)} km² for Spatial Mapping)`}
                  {aoiOverLimit && " — this AOI is too large; interpolation requests will be rejected."}
                </div>
              )}
            </div>
            <div className="ctrl-group">
              <label className="ctrl-label">
                Latitude Range (°)
                {spatialRangeIsAuto && <span style={{ fontWeight: 400, color: "var(--text-muted)" }}> (full available range)</span>}
              </label>
              <div className="date-row">
                <input className="ctrl-input" type="number" placeholder="Min" value={filters.latMin} onChange={(e) => update("latMin", e.target.value)} />
                <input className="ctrl-input" type="number" placeholder="Max" value={filters.latMax} onChange={(e) => update("latMax", e.target.value)} />
              </div>
            </div>
            <div className="ctrl-group">
              <label className="ctrl-label">
                Longitude Range (°)
                {spatialRangeIsAuto && <span style={{ fontWeight: 400, color: "var(--text-muted)" }}> (full available range)</span>}
              </label>
              <div className="date-row">
                <input className="ctrl-input" type="number" placeholder="Min" value={filters.lonMin} onChange={(e) => update("lonMin", e.target.value)} />
                <input className="ctrl-input" type="number" placeholder="Max" value={filters.lonMax} onChange={(e) => update("lonMax", e.target.value)} />
              </div>
            </div>
            <div className="ctrl-group">
              <label className="ctrl-label">Custom Boundary</label>
              <button
                className="btn-reset-ctrl"
                style={{ marginTop: 0, justifyContent: "center", display: "flex", alignItems: "center", gap: "0.4rem" }}
                onClick={() => boundaryFileInputRef.current?.click()}
              >
                📁 Upload Shapefile (.zip)
              </button>
              <input
                ref={boundaryFileInputRef}
                type="file"
                accept=".zip,.shp"
                style={{ display: "none" }}
                onChange={handleCustomBoundaryUpload}
              />
              {customBoundaryName && (
                <div className="gis-caption" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.4rem" }}>
                  <span>✓ {customBoundaryName} (active spatial filter)</span>
                  <button className="btn-clear-spatial" onClick={clearCustomBoundary}>
                    Clear
                  </button>
                </div>
              )}
            </div>
            <div className="ctrl-group">
              <label className="ctrl-label">Depth / Elevation (m)</label>
              <div className="date-row">
                <input className="ctrl-input" type="number" placeholder="Min" value={filters.depthMin} onChange={(e) => update("depthMin", e.target.value)} />
                <input className="ctrl-input" type="number" placeholder="Max" value={filters.depthMax} onChange={(e) => update("depthMax", e.target.value)} />
              </div>
            </div>
            <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", lineHeight: 1.5 }}>
              {filteredStations.length} station(s) match the current geospatial filters.
            </div>
          </FilterGroup>

          <FilterGroup title="Time Range" icon="🕐">
            {selectedDataset && !hasTemporalData ? (
              <p className="gis-caption" style={{ marginTop: 0, marginBottom: 0 }}>
                This dataset has no time dimension — a date range/resolution filter doesn&apos;t apply.
              </p>
            ) : (
              <>
                <div className="ctrl-group">
                  <label className="ctrl-label">
                    From
                    {dateRangeIsAuto && selectedDataset && <span style={{ fontWeight: 400, color: "var(--text-muted)" }}> (full range)</span>}
                  </label>
                  <input className="ctrl-input" type="date" value={filters.dateFrom} onChange={(e) => update("dateFrom", e.target.value)} />
                </div>
                <div className="ctrl-group">
                  <label className="ctrl-label">To</label>
                  <input className="ctrl-input" type="date" value={filters.dateTo} onChange={(e) => update("dateTo", e.target.value)} />
                </div>
                <div className="ctrl-group">
                  <label className="ctrl-label">Temporal Resolution</label>
                  <select className="ctrl-select" value={filters.resolution} onChange={(e) => update("resolution", e.target.value as typeof filters.resolution)}>
                    <option value="daily">Daily</option>
                    <option value="monthly">Monthly</option>
                    <option value="seasonal">Seasonal</option>
                    <option value="annual">Annual</option>
                  </select>
                </div>
                {dateRangeOverLimit && (
                  <p className="gis-caption" style={{ color: "var(--yellow)", marginBottom: 0 }}>
                    ⚠ Selected range ({dateRangeDays} days) exceeds this module&apos;s configured
                    maximum of {maxDateRangeDaysForModule} days — the request will be rejected.
                    Narrow the range before submitting.
                  </p>
                )}
              </>
            )}
          </FilterGroup>

          <FilterGroup title="Analysis Options" icon="⚙️">
            <div className="ctrl-checkbox-row">
              <input type="checkbox" id="showTrend" checked={showTrend} onChange={(e) => setShowTrend(e.target.checked)} />
              <label htmlFor="showTrend">Show trend line</label>
            </div>
            <div className="ctrl-checkbox-row">
              <input type="checkbox" id="showMA" checked={showMA} onChange={(e) => setShowMA(e.target.checked)} />
              <label htmlFor="showMA">Moving average (3-month)</label>
            </div>
            <p className="gis-caption" style={{ marginTop: "0.6rem" }}>
              Color scale for each chart can be set from that chart&apos;s own toolbar.
            </p>
          </FilterGroup>
        </aside>

        <main className="viz-canvas">
          {!selectedDataset ? (
            <div className="empty-state">
              <div className="es-icon">📦</div>
              <p>
                {datasetsLoading
                  ? "Loading available datasets…"
                  : "Select a dataset from the Filters panel to begin exploring its approved variables."}
              </p>
            </div>
          ) : (
            <>
              <div className={`viz-module${module === "temporal" ? " active" : ""}`}>
                {module === "temporal" && (
                  <TemporalModule filters={filters} showTrend={showTrend} showMA={showMA} />
                )}
              </div>
              <div className={`viz-module${module === "spatial" ? " active" : ""}`}>
                {module === "spatial" && spatial.mapPanel}
              </div>
              <div className={`viz-module${module === "comparison" ? " active" : ""}`}>
                {module === "comparison" && (
                  <ComparisonModule filters={filters} availableParameters={availableParams} />
                )}
              </div>
              <div className={`viz-module${module === "statistics" ? " active" : ""}`}>
                {module === "statistics" && <StatisticsModule filters={filters} />}
              </div>
              <div className={`viz-module${module === "profiles" ? " active" : ""}`}>
                {module === "profiles" && (
                  <ProfilesModule filters={filters} availableParameters={availableParams} />
                )}
              </div>
            </>
          )}
        </main>

        {module === "spatial" && selectedDataset && (
          <aside className="ctrl-panel ctrl-panel-right">{spatial.toolsPanel}</aside>
        )}
      </div>
    </>
  );
}
