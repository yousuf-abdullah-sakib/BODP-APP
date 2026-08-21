"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import CategoryPill from "@/components/ui/CategoryPill";
import StatusBadge from "@/components/ui/StatusBadge";
import { useSession } from "@/context/SessionContext";
import { useToast } from "@/context/ToastContext";
import type { DatasetDetail } from "@/lib/types/catalog";
import DatasetRequestModal from "./DatasetRequestModal";
import { useDatasetFilters } from "./useDatasetFilters";
import { boundsOf, type SpatialAOI } from "@/lib/geo/spatialAoi";
import { getPreferences } from "@/lib/api/me";
import { formatCoordinate, formatDate, type CoordinateFormatPreference, type DateFormatPreference } from "@/lib/format";
import { parseShapefile, hasUsableGeometry } from "@/lib/geo/shapefileUpload";

const SpatialFilterMap = dynamic(() => import("@/components/map/SpatialFilterMap"), {
  ssr: false,
  loading: () => (
    <div style={{ height: 200, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: "0.75rem" }}>
      Loading map…
    </div>
  ),
});

function FilterSection({
  title,
  icon,
  defaultOpen = true,
  children,
}: {
  title: string;
  icon: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="sidebar-section">
      <h4 className="filter-section-toggle" onClick={() => setOpen((o) => !o)}>
        <span>
          {icon} {title}
        </span>
        <span className="filter-section-caret">{open ? "▾" : "▸"}</span>
      </h4>
      {open && children}
    </div>
  );
}

export default function DatasetDetailClient({ dataset }: { dataset: DatasetDetail }) {
  const { toast } = useToast();
  const { user } = useSession();
  const router = useRouter();
  const [showRequest, setShowRequest] = useState(false);
  const [clearSignal, setClearSignal] = useState(0);
  const [dateFormat, setDateFormat] = useState<DateFormatPreference>("iso");
  const [coordFormat, setCoordFormat] = useState<CoordinateFormatPreference>("dd");
  const {
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
  } = useDatasetFilters(dataset);

  // Custom Boundary upload — same parseShapefile logic/component the
  // Visualize page's Spatial Mapping module already uses, wired into the
  // SAME AOI pipeline a hand-drawn shape uses: the uploaded polygon is
  // reduced to bounds via boundsOf and written to filters.bounds exactly
  // like SpatialFilterMap's onAoiChange already does below, so it rides
  // the existing spatial filter (and thus coverage/visualization query)
  // with no separate logic. uploadedBoundaryAoi is kept only so
  // SpatialFilterMap knows to draw THIS shape specifically; it's cleared
  // whenever the user draws something new or hits Clear, so the
  // boundary stays active until the user changes or clears it.
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
        applyAoiBounds(nextAoi);
        toast(`Loaded boundary from ${f.name} — now applied as the active spatial filter.`, "success");
      }
    } catch {
      toast("Could not read that file as a shapefile. Upload a .zip (shp+dbf+prj) bundle.", "error");
    }
    e.target.value = "";
  }

  function clearCustomBoundary() {
    clearSpatial();
  }

  // Schema-driven filter rendering (PLAN.md Phase 4) — schema is null for
  // any dataset an admin hasn't reviewed yet (Phase 3), which keeps every
  // section below exactly as it always rendered (the fallback). Once
  // reviewed, gating keys off each variable's ADMIN-APPROVED roles
  // (v.roles), never Phase 2's raw is_dimension detection flag — an admin
  // can approve a Phase-2-detected dimension (e.g. time) as a
  // data_variable instead, and that must NOT render a Time Range filter;
  // only an explicit "dimension" role does.
  const approvedNonDimensionNames = schema
    ? schema.variables.filter((v) => !v.roles.includes("dimension") && v.roles.length > 0).map((v) => v.name)
    : null;
  const hasApprovedTimeDimension = schema
    ? schema.variables.some((v) => v.roles.includes("dimension") && v.data_type === "temporal")
    : true;
  // Matches the backend's own lat/lon column-alias detection exactly
  // (see backend/app/services/parsers/csv_parser.py's _LAT_ALIASES/
  // _LON_ALIASES and scope_filter.py's identical list) — a dataset
  // ingested with "latitude"/"longitude" headers instead of "lat"/"lon"
  // is just as spatially filterable, and must not silently lose its
  // Geographic Bounding Box section just because the column happened to
  // be named the long way.
  const _LAT_NAMES = new Set(["lat", "latitude", "y"]);
  const _LON_NAMES = new Set(["lon", "lng", "longitude", "x"]);
  const hasApprovedSpatialDimension = schema
    ? schema.variables.some(
        (v) =>
          v.roles.includes("dimension") &&
          (_LAT_NAMES.has(v.name.toLowerCase()) || _LON_NAMES.has(v.name.toLowerCase()))
      )
    : true;
  const hasApprovedDepthDimension = schema
    ? schema.variables.some((v) => v.roles.includes("dimension") && v.name === "depth")
    : true;

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    getPreferences()
      .then((prefs) => {
        if (cancelled) return;
        setDateFormat(prefs.date_format as DateFormatPreference);
        setCoordFormat(prefs.coordinate_format as CoordinateFormatPreference);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user]);

  // Mirrors VisualizeClient.tsx's handleAoiChange exactly: an AOI (drawn
  // or uploaded) drives BOTH filters.bounds (the precise polygon-derived
  // bbox the query actually uses) AND the plain Latitude/Longitude Min/
  // Max text fields, via the same boundsOf() reduction already computed
  // once here and reused for both — so the visible fields always show
  // what the map/upload just set, exactly like Visualization already
  // does, instead of only updating the query silently underneath.
  function applyAoiBounds(aoi: SpatialAOI) {
    const bounds = boundsOf(aoi);
    update("bounds", bounds);
    update("latMin", bounds.latMin.toFixed(3));
    update("latMax", bounds.latMax.toFixed(3));
    update("lonMin", bounds.lonMin.toFixed(3));
    update("lonMax", bounds.lonMax.toFixed(3));
  }

  // Clearing reverts the Lat/Lon fields to the dataset's own real extent
  // (not blank) — matching this page's own auto-fill convention for
  // "no active user restriction" (Visualization's equivalent clearAoi()
  // blanks the fields instead, since it has no per-dataset default to
  // revert to).
  function clearSpatial() {
    setClearSignal((s) => s + 1);
    update("bounds", null);
    resetSpatialToExtent();
    setCustomBoundaryName("");
    setUploadedBoundaryAoi(null);
  }

  // Hand-drawing a new shape abandons any active uploaded boundary --
  // "the user changes it" -- so this (not a raw applyAoiBounds(...) call
  // inline) is what SpatialFilterMap's onAoiChange is wired to.
  function handleUserDrawnAoiChange(aoi: SpatialAOI | null) {
    setCustomBoundaryName("");
    setUploadedBoundaryAoi(null);
    if (aoi) {
      applyAoiBounds(aoi);
    } else {
      update("bounds", null);
      resetSpatialToExtent();
    }
  }

  function handleResetAll() {
    resetFilters();
    setClearSignal((s) => s + 1);
    setCustomBoundaryName("");
    setUploadedBoundaryAoi(null);
  }

  return (
    <>
      <div className="page-hero">
        <div className="section-tag">
          <Link href="/catalog" style={{ color: "inherit", textDecoration: "none" }}>
            ← Back to Catalog
          </Link>
        </div>
        <h1>{dataset.title}</h1>
        <p>{dataset.description}</p>
        <div className="ph-meta">
          <div className="ph-meta-item">
            Category: <b>{dataset.category ?? "Uncategorized"}</b>
          </div>
          <div className="ph-meta-item">
            Source: <b>{dataset.source ?? "—"}</b>
          </div>
          <div className="ph-meta-item">
            Records: <b>{dataset.record_count.toLocaleString()}</b>
          </div>
          <div className="ph-meta-item">
            Resolution: <b>{dataset.resolution ?? "—"}</b>
          </div>
          <div className="ph-meta-item">
            Formats: <b>{dataset.formats.join(", ") || "—"}</b>
          </div>
          <div className="ph-meta-item">
            License: <b>{dataset.license ?? "—"}</b>
          </div>
          <div className="ph-meta-item">
            Updated: <b>{formatDate(dataset.updated_at, dateFormat)}</b>
          </div>
        </div>
      </div>

      <div className="main-layout">
        <aside className="sidebar">
          <div className="sidebar-section" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <h4 style={{ margin: 0, borderBottom: "none", paddingBottom: 0 }}>
              🧰 Filters {activeCount > 0 && <span className="chip">{activeCount} active</span>}
            </h4>
            <button className="btn-clear-spatial" onClick={handleResetAll}>
              Reset All
            </button>
          </div>

          {hasApprovedSpatialDimension && (
            <FilterSection title="Geographic Bounding Box" icon="🗺️">
              <div className="map-filter-box">
                <div className="map-filter-head">
                  <span>Draw a box on the map</span>
                  <button className="btn-clear-spatial" onClick={clearSpatial}>
                    Clear
                  </button>
                </div>
                <SpatialFilterMap
                  onAoiChange={handleUserDrawnAoiChange}
                  clearSignal={clearSignal}
                  stations={stationOptions}
                  externalAoi={uploadedBoundaryAoi}
                />
                <div className="map-filter-info">
                  {filters.bounds ? (
                    <>
                      <b>Selected:</b> Lat {formatCoordinate(filters.bounds.latMin, "lat", coordFormat)}–
                      {formatCoordinate(filters.bounds.latMax, "lat", coordFormat)}, Lon{" "}
                      {formatCoordinate(filters.bounds.lonMin, "lon", coordFormat)}–
                      {formatCoordinate(filters.bounds.lonMax, "lon", coordFormat)}
                    </>
                  ) : (
                    "No area selected — showing all locations"
                  )}
                </div>
              </div>
              <div className="filter-group" style={{ marginTop: "0.8rem" }}>
                <span className="filter-label">
                  Latitude Range (°)
                  {spatialRangeIsAuto && <span style={{ fontWeight: 400, color: "var(--text-muted)" }}> (full available range)</span>}
                </span>
                <div className="date-row">
                  <input className="filter-input" type="number" placeholder="Min" value={filters.latMin} onChange={(e) => update("latMin", e.target.value)} />
                  <input className="filter-input" type="number" placeholder="Max" value={filters.latMax} onChange={(e) => update("latMax", e.target.value)} />
                </div>
              </div>
              <div className="filter-group">
                <span className="filter-label">
                  Longitude Range (°)
                  {spatialRangeIsAuto && <span style={{ fontWeight: 400, color: "var(--text-muted)" }}> (full available range)</span>}
                </span>
                <div className="date-row">
                  <input className="filter-input" type="number" placeholder="Min" value={filters.lonMin} onChange={(e) => update("lonMin", e.target.value)} />
                  <input className="filter-input" type="number" placeholder="Max" value={filters.lonMax} onChange={(e) => update("lonMax", e.target.value)} />
                </div>
              </div>
              <div className="filter-group">
                <span className="filter-label">Custom Boundary</span>
                <button
                  className="btn-reset"
                  style={{ width: "100%", justifyContent: "center", display: "flex", alignItems: "center", gap: "0.4rem" }}
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
                  <div className="map-filter-info" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.4rem", marginTop: "0.5rem" }}>
                    <span>✓ {customBoundaryName} (active spatial filter)</span>
                    <button className="btn-clear-spatial" onClick={clearCustomBoundary}>
                      Clear
                    </button>
                  </div>
                )}
              </div>
              {hasApprovedDepthDimension && (
                <div className="filter-group">
                  <span className="filter-label">Depth / Elevation (m)</span>
                  <div className="date-row">
                    <input className="filter-input" type="number" placeholder="Min" value={filters.depthMin} onChange={(e) => update("depthMin", e.target.value)} />
                    <input className="filter-input" type="number" placeholder="Max" value={filters.depthMax} onChange={(e) => update("depthMax", e.target.value)} />
                  </div>
                </div>
              )}
              <div className="filter-group">
                <span className="filter-label">Station</span>
                <select className="filter-select" value={filters.station} onChange={(e) => update("station", e.target.value)}>
                  <option value="">All Stations</option>
                  {stationOptions.map((s) => (
                    <option key={s.code} value={s.code}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
            </FilterSection>
          )}

          {hasApprovedTimeDimension && (
            <FilterSection title="Time Range" icon="📅">
              {hasTemporalData ? (
                <div className="filter-group">
                  <span className="filter-label">
                    Date From – To
                    {dateRangeIsAuto && <span style={{ fontWeight: 400, color: "var(--text-muted)" }}> (full available range)</span>}
                  </span>
                  <div className="date-row">
                    <input className="filter-input" type="date" value={filters.dateFrom} onChange={(e) => update("dateFrom", e.target.value)} />
                    <input className="filter-input" type="date" value={filters.dateTo} onChange={(e) => update("dateTo", e.target.value)} />
                  </div>
                </div>
              ) : (
                <p className="filter-label" style={{ color: "var(--text-muted)", fontWeight: 400 }}>
                  This dataset has no time dimension — a date range filter doesn&apos;t apply.
                </p>
              )}
            </FilterSection>
          )}

          <FilterSection title="Variable & Quality" icon="📐">
            <div className="filter-group">
              <span className="filter-label">
                Parameter / Variable {filters.parameters.length > 0 && <span className="chip">{filters.parameters.length} selected</span>}
              </span>
              <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                {(approvedNonDimensionNames ?? dataset.parameters).map((p) => {
                  const checked = filters.parameters.includes(p);
                  return (
                    <label key={p} style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.82rem", cursor: "pointer" }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          update(
                            "parameters",
                            checked ? filters.parameters.filter((x) => x !== p) : [...filters.parameters, p]
                          )
                        }
                      />
                      {p}
                    </label>
                  );
                })}
                {(approvedNonDimensionNames ?? dataset.parameters).length === 0 && (
                  <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>No parameters available.</span>
                )}
              </div>
              <p style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
                None selected includes all parameters. Select one or more to filter.
              </p>
            </div>
            <div className="filter-group">
              <span className="filter-label">Quality Level</span>
              <select className="filter-select" value={filters.quality} onChange={(e) => update("quality", e.target.value)}>
                <option value="">All Status</option>
                <option value="normal">✓ Normal</option>
                <option value="caution">⚠ Caution</option>
                <option value="alert">✗ Alert</option>
              </select>
            </div>
            <div className="filter-group">
              <span className="filter-label">Processing Level</span>
              <select className="filter-select" value={filters.processingLevel} onChange={(e) => update("processingLevel", e.target.value)}>
                <option value="">All Levels</option>
                {dataset.processing_levels.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
          </FilterSection>

          <FilterSection title="Source & Platform" icon="🛰️" defaultOpen={false}>
            <div className="filter-group">
              <span className="filter-label">Data Source</span>
              <select className="filter-select" value={filters.source} onChange={(e) => update("source", e.target.value)}>
                <option value="">Any Source</option>
                {dataset.source && <option value={dataset.source}>{dataset.source}</option>}
              </select>
            </div>
            <div className="filter-group">
              <span className="filter-label">Observation Platform</span>
              <select className="filter-select" value={filters.platform} onChange={(e) => update("platform", e.target.value)}>
                <option value="">All Platforms</option>
                {dataset.platforms.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div className="filter-group">
              <span className="filter-label">Data Format</span>
              <select className="filter-select" value={filters.format} onChange={(e) => update("format", e.target.value)}>
                <option value="">All Formats</option>
                {dataset.formats.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </div>
          </FilterSection>
        </aside>

        <main className="content-area">
          <div className="summary-strip">
            <div className="sum-card">
              <div className="sum-num">{matchingCount.toLocaleString()}</div>
              <div className="sum-lbl">Matching Records</div>
            </div>
            <div className="sum-card">
              <div className="sum-num" style={{ color: "var(--green)" }}>
                {qualityBreakdown.normal.toLocaleString()}
              </div>
              <div className="sum-lbl">Normal</div>
            </div>
            <div className="sum-card">
              <div className="sum-num" style={{ color: "var(--yellow)" }}>
                {qualityBreakdown.caution.toLocaleString()}
              </div>
              <div className="sum-lbl">Caution</div>
            </div>
            <div className="sum-card">
              <div className="sum-num" style={{ color: "var(--red)" }}>
                {qualityBreakdown.alert.toLocaleString()}
              </div>
              <div className="sum-lbl">Alert</div>
            </div>
            <div className="sum-card">
              <div className="sum-num" style={{ color: "var(--accent-light)" }}>
                {Math.round((matchingCount / Math.max(1, datasetTotalCount)) * 100)}%
              </div>
              <div className="sum-lbl">Of Dataset</div>
            </div>
          </div>

          <div className="panel" style={{ marginBottom: "1.4rem" }}>
            <div className="panel-head">
              <span className="panel-title">Data Preview</span>
              <span className="chip">
                Showing {preview.length} of {matchingCount.toLocaleString()} — request access for full data
              </span>
            </div>
            <div className="panel-body" style={{ padding: 0 }}>
              {loading ? (
                <div className="empty-state">
                  <div className="es-icon">⏳</div>
                  <p>Loading records…</p>
                </div>
              ) : preview.length === 0 ? (
                <div className="empty-state">
                  <div className="es-icon">🔍</div>
                  <p>No records match your filters.</p>
                </div>
              ) : (
                <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Station</th>
                        <th>Depth (m)</th>
                        <th>Parameter</th>
                        <th>Value</th>
                        <th>Unit</th>
                        <th>Platform</th>
                        <th>Format</th>
                        <th>Level</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.map((r) => (
                        <tr key={r.id}>
                          <td style={{ fontSize: "0.8rem", whiteSpace: "nowrap" }}>
                            {r.time ? formatDate(r.time, dateFormat) : "—"}
                          </td>
                          <td style={{ fontSize: "0.82rem" }}>{r.location}</td>
                          <td style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>{r.depth_m ?? "—"}</td>
                          <td style={{ fontWeight: 500 }}>{r.parameter}</td>
                          <td style={{ fontWeight: 700, color: "var(--accent-light)" }}>{r.value}</td>
                          <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>{r.unit}</td>
                          <td style={{ fontSize: "0.76rem", color: "var(--text-muted)" }}>{r.platform}</td>
                          <td style={{ fontSize: "0.76rem", color: "var(--text-muted)" }}>{r.format}</td>
                          <td style={{ fontSize: "0.72rem", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{r.processing_level}</td>
                          <td>
                            <StatusBadge status={r.quality_flag} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">About This Dataset</span>
              <CategoryPill category={dataset.category} />
            </div>
            <div className="panel-body">
              <p style={{ fontSize: "0.84rem", color: "var(--text-secondary)", lineHeight: 1.7, marginBottom: "1rem" }}>
                {dataset.description}
              </p>
              <div className="mini-label">Parameters</div>
              <div className="ds-card-params" style={{ marginBottom: "0.9rem" }}>
                {dataset.parameters.map((p) => (
                  <span key={p} className="chip">
                    {p}
                  </span>
                ))}
              </div>
              <div className="mini-label">Observation Platforms</div>
              <div className="ds-card-params" style={{ marginBottom: "0.9rem" }}>
                {dataset.platforms.map((p) => (
                  <span key={p} className="chip">
                    {p}
                  </span>
                ))}
              </div>
              <div className="mini-label">Processing Levels</div>
              <div className="ds-card-params">
                {dataset.processing_levels.map((p) => (
                  <span key={p} className="chip">
                    {p}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </main>
      </div>

      <div className="selection-bar visible">
        <div className="sel-info">
          🔒 Full dataset ({dataset.record_count.toLocaleString()} records) requires an approved access request.
        </div>
        <div className="sel-actions">
          <button
            className="btn-request btn-dl"
            onClick={() => {
              if (!user) {
                toast("Please sign in to request dataset access.", "info");
                router.push("/login");
                return;
              }
              if (matchingCount === 0) {
                toast("No records match your current filters — adjust filters before requesting.", "error");
                return;
              }
              setShowRequest(true);
            }}
          >
            📋 Request Access
          </button>
        </div>
      </div>

      {showRequest && (
        <DatasetRequestModal
          dataset={dataset}
          criteria={filters}
          onClose={() => setShowRequest(false)}
          onSubmitted={() => setShowRequest(false)}
        />
      )}

      <div className="data-footer">
        <span>© 2024 Bangladesh Oceanographic Data Portal — CC BY 4.0</span>
      </div>
    </>
  );
}
