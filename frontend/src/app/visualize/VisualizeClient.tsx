"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { getCatalogTaxonomy } from "@/lib/api/catalog";
import type { TaxonomyOptions } from "@/lib/types/catalog";
import { useVizFilters } from "./useVizFilters";
import { useVizComputeLimits } from "./useVizComputeLimits";
import TemporalModule from "./modules/TemporalModule";
import { useSpatialMapping } from "./modules/SpatialMappingModule";
import ComparisonModule from "./modules/ComparisonModule";
import StatisticsModule from "./modules/StatisticsModule";
import { boundsOf, type SpatialAOI } from "@/lib/geo/spatialAoi";
import { bboxAreaKm2 } from "@/lib/geo/bboxArea";

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
] as const;

type ModuleKey = (typeof MODULES)[number]["key"];

function FilterGroup({ title, icon, defaultOpen = true, children }: { title: string; icon: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="ctrl-section">
      <div className="ctrl-heading ctrl-heading-toggle" onClick={() => setOpen((o) => !o)}>
        <span>
          {icon} {title}
        </span>
        <span className="filter-section-caret">{open ? "▾" : "▸"}</span>
      </div>
      {open && children}
    </div>
  );
}

export default function VisualizeClient() {
  const [module, setModule] = useState<ModuleKey>("temporal");
  const { filters, update, resetFilters, filteredStations, activeCount } = useVizFilters();
  const limits = useVizComputeLimits();
  const [showTrend, setShowTrend] = useState(true);
  const [showMA, setShowMA] = useState(true);
  const [aoiClearSignal, setAoiClearSignal] = useState(0);
  const [aoi, setAoi] = useState<SpatialAOI | null>(null);
  const [taxonomy, setTaxonomy] = useState<TaxonomyOptions | null>(null);

  useEffect(() => {
    getCatalogTaxonomy()
      .then(setTaxonomy)
      .catch(() => setTaxonomy(null));
  }, []);

  const availableParams = taxonomy?.parameters ?? [];

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

  function clearAoi() {
    setAoiClearSignal((s) => s + 1);
    setAoi(null);
    update("latMin", "");
    update("latMax", "");
    update("lonMin", "");
    update("lonMax", "");
  }

  const spatial = useSpatialMapping({ parameter: filters.parameter, filteredStations, aoi, filters });

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
            <button className="btn-reset-ctrl" style={{ width: "auto", padding: "0.3rem 0.7rem", marginTop: 0 }} onClick={() => { resetFilters(); setAoi(null); setAoiClearSignal((s) => s + 1); }}>
              Reset
            </button>
          </div>

          <FilterGroup title="Dataset" icon="📦">
            <div className="ctrl-group">
              <label className="ctrl-label">Category</label>
              <select className="ctrl-select" value={filters.category} onChange={(e) => update("category", e.target.value)}>
                <option value="">All Categories</option>
                {(taxonomy?.categories ?? []).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="ctrl-group">
              <label className="ctrl-label">Parameter / Variable</label>
              <select className="ctrl-select" value={filters.parameter} onChange={(e) => update("parameter", e.target.value)}>
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
          </FilterGroup>

          <FilterGroup title="Time Range" icon="🕐">
            <div className="ctrl-group">
              <label className="ctrl-label">From</label>
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
          </FilterGroup>

          <FilterGroup title="Area of Interest (AOI)" icon="🌍" defaultOpen={false}>
            <div className="map-filter-box">
              <div className="map-filter-head">
                <span>Draw a box or polygon to define your AOI</span>
                <button className="btn-clear-spatial" onClick={clearAoi}>
                  Clear
                </button>
              </div>
              <SpatialFilterMap onAoiChange={handleAoiChange} clearSignal={aoiClearSignal} enablePolygon stations={filteredStations} />
              <div className="map-filter-info">
                {aoi ? (
                  aoi.kind === "polygon" ? (
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
              <label className="ctrl-label">Latitude Range (°)</label>
              <div className="date-row">
                <input className="ctrl-input" type="number" placeholder="Min" value={filters.latMin} onChange={(e) => update("latMin", e.target.value)} />
                <input className="ctrl-input" type="number" placeholder="Max" value={filters.latMax} onChange={(e) => update("latMax", e.target.value)} />
              </div>
            </div>
            <div className="ctrl-group">
              <label className="ctrl-label">Longitude Range (°)</label>
              <div className="date-row">
                <input className="ctrl-input" type="number" placeholder="Min" value={filters.lonMin} onChange={(e) => update("lonMin", e.target.value)} />
                <input className="ctrl-input" type="number" placeholder="Max" value={filters.lonMax} onChange={(e) => update("lonMax", e.target.value)} />
              </div>
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

          <FilterGroup title="Analysis Options" icon="⚙️" defaultOpen={false}>
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
        </main>

        {module === "spatial" && <aside className="ctrl-panel ctrl-panel-right">{spatial.toolsPanel}</aside>}
      </div>
    </>
  );
}
