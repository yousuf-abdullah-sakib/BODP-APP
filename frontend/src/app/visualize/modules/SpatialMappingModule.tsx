"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import PlotlyChart from "@/components/charts/PlotlyChart";
import ChartToolbar, { useChartControls } from "@/components/charts/ChartToolbar";
import ColorRampSelect from "@/components/ui/ColorRampSelect";
import { useFullscreenChart, FullscreenButton, FullscreenOverlay } from "@/components/charts/FullscreenChartWrapper";
import { parseShapefile } from "@/lib/geo/shapefileUpload";
import { equalIntervalBreaks, type ColorRampName, type InterpolationDisplayMode } from "@/lib/geo/colorRamp";
import type { InterpolationOutput, BaseMapName, GisMapApi } from "@/components/map/GisSpatialMap";
import type { SpatialAOI } from "@/lib/geo/spatialAoi";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { postSpatial, getSpatialJob } from "@/lib/api/visualize";
import { getDefaultBoundary } from "@/lib/api/boundary";
import { toVizFilterParams, type VizFilters } from "../useVizFilters";
import { useVizExportSettings } from "../useVizExportSettings";
import type { InterpolationMethod, SpatialGrid, SpatialPoint } from "@/lib/types/visualize";
import type { StationOption } from "@/lib/types/catalog";
import type { Data } from "plotly.js";

const GisSpatialMap = dynamic(() => import("@/components/map/GisSpatialMap"), {
  ssr: false,
  loading: () => (
    <div style={{ height: 560, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)" }}>
      Loading map…
    </div>
  ),
});

const BASE_MAPS: { key: BaseMapName; label: string; icon: string }[] = [
  { key: "light", label: "Light", icon: "☀️" },
  { key: "dark", label: "Dark", icon: "🌙" },
  { key: "satellite", label: "Satellite", icon: "🛰️" },
  { key: "terrain", label: "Terrain", icon: "⛰️" },
];

// Bay of Bengal / Bangladesh coastal zone — default request bbox when no
// AOI is drawn (matches the prototype's hardcoded interpolation extent).
const DEFAULT_BOUNDS = { lat_min: 20.5, lat_max: 23.0, lon_min: 88.0, lon_max: 92.5 };

const POLL_INTERVAL_MS = 1500;

function ToolGroup({ title, icon, defaultOpen = true, children }: { title: string; icon: string; defaultOpen?: boolean; children: ReactNode }) {
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

interface UseSpatialMappingArgs {
  parameter: string;
  filteredStations: StationOption[];
  aoi: SpatialAOI | null;
  filters: VizFilters;
}

export function useSpatialMapping({ parameter, filteredStations, aoi, filters }: UseSpatialMappingArgs) {
  const { toast } = useToast();
  const exportsEnabled = useVizExportSettings();
  const [defaultBoundary, setDefaultBoundary] = useState<GeoJSON.FeatureCollection | null>(null);
  const [defaultBoundaryName, setDefaultBoundaryName] = useState<string | null>(null);
  const [ramp, setRamp] = useState<ColorRampName>("Viridis");
  const [method, setMethod] = useState<InterpolationMethod>("idw");
  const [resolution, setResolution] = useState(40);
  const [output, setOutput] = useState<InterpolationOutput>("contour");
  const [displayMode, setDisplayMode] = useState<InterpolationDisplayMode>("continuous");
  const [classCount, setClassCount] = useState(5);
  const [breakpointsText, setBreakpointsText] = useState("");
  const [baseMap, setBaseMap] = useState<BaseMapName>("light");
  const [interpolationOpacity, setInterpolationOpacity] = useState(0.85);
  const [pointsOpacity, setPointsOpacity] = useState(0.82);
  const [showInterpolation, setShowInterpolation] = useState(true);
  const [showPoints, setShowPoints] = useState(false);
  const [showBaseBoundary, setShowBaseBoundary] = useState(true);
  const [uploadedBoundary, setUploadedBoundary] = useState<GeoJSON.FeatureCollection | null>(null);
  const [uploadedName, setUploadedName] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const mapApiRef = useRef<GisMapApi | null>(null);
  const mapFullscreen = useFullscreenChart();
  const latProfileFullscreen = useFullscreenChart();
  const lonProfileFullscreen = useFullscreenChart();
  const latProfileControls = useChartControls({ grid: true, resetView: true });
  const lonProfileControls = useChartControls({ grid: true, resetView: true });

  const [points, setPoints] = useState<SpatialPoint[]>([]);
  const [grid, setGrid] = useState<SpatialGrid | null>(null);
  const [methodUsed, setMethodUsed] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDefaultBoundary()
      .then((b) => {
        if (b) {
          setDefaultBoundary(b.geojson);
          setDefaultBoundaryName(b.name);
        }
      })
      .catch(() => {});
  }, []);

  const bounds = useMemo(() => {
    if (aoi) {
      const lats = aoi.kind === "rectangle" ? [aoi.bounds.latMin, aoi.bounds.latMax] : aoi.ring.map((p) => p[0]);
      const lons = aoi.kind === "rectangle" ? [aoi.bounds.lonMin, aoi.bounds.lonMax] : aoi.ring.map((p) => p[1]);
      return {
        lat_min: Math.min(...lats),
        lat_max: Math.max(...lats),
        lon_min: Math.min(...lons),
        lon_max: Math.max(...lons),
      };
    }
    return DEFAULT_BOUNDS;
  }, [aoi]);

  const breakpoints = useMemo(
    () =>
      breakpointsText
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((n) => !Number.isNaN(n))
        .sort((a, b) => a - b),
    [breakpointsText]
  );

  useEffect(() => {
    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    setLoading(true);
    setError(null);

    async function run() {
      try {
        const res = await postSpatial({
          parameter,
          method,
          grid_resolution: resolution,
          bounds,
          ...toVizFilterParams(filters),
        });

        if (res.status === "complete") {
          if (!cancelled) {
            setPoints(res.points ?? []);
            setGrid(res.grid ?? null);
            setMethodUsed(res.method_used ?? null);
            setLoading(false);
          }
          return;
        }

        const jobId = res.job_id;
        if (!jobId) return;

        async function poll() {
          const job = await getSpatialJob(jobId!);
          if (cancelled) return;
          if (job.status === "complete") {
            setPoints(job.points ?? []);
            setGrid(job.grid ?? null);
            setMethodUsed(job.method_used ?? null);
            setLoading(false);
          } else if (job.status === "failed") {
            setError(job.error_message ?? "Interpolation failed.");
            setLoading(false);
          } else {
            pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
          }
        }
        await poll();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Failed to load spatial data.");
          setLoading(false);
        }
      }
    }

    run();
    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [parameter, method, resolution, bounds, filters]);

  const allowedCodes = useMemo(() => new Set(filteredStations.map((s) => s.code)), [filteredStations]);
  const gisPoints = useMemo(() => {
    return points
      .filter((p) => filteredStations.length === 0 || allowedCodes.has(p.station))
      .map((p) => ({ station: p.station, lat: p.lat, lon: p.lon, value: p.value, sizeValue: p.value }));
  }, [points, allowedCodes, filteredStations.length]);

  const values = gisPoints.map((p) => p.value);
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;
  const classBreaks = useMemo(() => equalIntervalBreaks(min, max, classCount), [min, max, classCount]);

  const latProfile = useMemo(() => (grid ? grid.lats.map((lat, i) => ({ lat, value: mean(grid.z[i]) })) : []), [grid]);
  const lonProfile = useMemo(
    () => (grid ? grid.lons.map((lon, j) => ({ lon, value: mean(grid.z.map((row) => row[j])) })) : []),
    [grid]
  );

  function mean(xs: number[]): number {
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
  }

  const latProfileData: Data[] = [{ x: latProfile.map((p) => p.lat), y: latProfile.map((p) => p.value), type: "scatter", mode: "lines" } as Data];
  const lonProfileData: Data[] = [{ x: lonProfile.map((p) => p.lon), y: lonProfile.map((p) => p.value), type: "scatter", mode: "lines" } as Data];

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const geo = await parseShapefile(f);
      setUploadedBoundary(geo);
      setUploadedName(f.name);
      toast(`Loaded boundary from ${f.name}. Interpolation is now clipped to this area.`, "success");
    } catch {
      toast("Could not read that file as a shapefile. Upload a .zip (shp+dbf+prj) bundle.", "error");
    }
    e.target.value = "";
  }

  function clearUpload() {
    setUploadedBoundary(null);
    setUploadedName("");
  }

  const mapNode = (h: number) => (
    <GisSpatialMap
      points={gisPoints}
      grid={grid}
      ramp={ramp}
      showInterpolation={showInterpolation}
      interpolationOutput={output}
      displayMode={displayMode}
      classCount={classCount}
      breakpoints={breakpoints}
      uploadedBoundary={uploadedBoundary}
      defaultBoundaryOverride={defaultBoundary}
      showBaseBoundary={showBaseBoundary}
      showPoints={showPoints}
      aoi={aoi}
      baseMap={baseMap}
      interpolationOpacity={interpolationOpacity}
      pointsOpacity={pointsOpacity}
      height={h}
      mapApiRef={mapApiRef}
    />
  );

  const aoiDescription = uploadedName
    ? `Clipped to ${uploadedName}`
    : aoi
      ? aoi.kind === "polygon"
        ? "Clipped to selected polygon Area of Interest"
        : "Clipped to selected Area of Interest"
      : "Bay of Bengal · Bangladesh Coastal Zone";

  const mapPanel = (
    <div className="sm-map-panel">
      <div className="chart-container">
        <div className="chart-head">
          <div>
            <div className="chart-title">{parameter} — Spatial Distribution</div>
            <div className="chart-subtitle">
              {aoiDescription} · {gisPoints.length} stations
              {methodUsed && methodUsed !== method && ` · showing ${methodUsed.toUpperCase()}`}
            </div>
          </div>
          <div className="chart-actions">
            <FullscreenButton onClick={mapFullscreen.expand} />
          </div>
        </div>
        <div className="map-toolbar">
          <button className="map-tool-btn" title="Zoom to full extent" onClick={() => mapApiRef.current?.zoomToFullExtent()}>
            🌐 Full Extent
          </button>
          <button className="map-tool-btn" title="Zoom to active layer" onClick={() => mapApiRef.current?.zoomToLayer()}>
            🔍 Zoom to Layer
          </button>
          <button className="map-tool-btn" title="Zoom to selected Area of Interest" onClick={() => mapApiRef.current?.zoomToSelection()} disabled={!aoi}>
            🎯 Zoom to Selection
          </button>
          <button className="map-tool-btn" title="Fit to boundary" onClick={() => mapApiRef.current?.fitToBoundary()}>
            🗺️ Fit to Boundary
          </button>
          <button className="map-tool-btn" title="Reset map view" onClick={() => mapApiRef.current?.zoomToFullExtent()}>
            ⟲ Reset View
          </button>
        </div>
        {loading ? (
          <div style={{ height: 560, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)" }}>
            ⏳ Computing interpolation…
          </div>
        ) : error ? (
          <div style={{ height: 560, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)" }}>
            ⚠️ {error}
          </div>
        ) : (
          mapNode(560)
        )}
      </div>
      {mapFullscreen.expanded && (
        <FullscreenOverlay title={`${parameter} — Spatial Distribution`} onClose={mapFullscreen.collapse}>
          {mapNode(800)}
        </FullscreenOverlay>
      )}

      <div className="chart-row-2" style={{ marginTop: "1.2rem" }}>
        <div className="chart-container">
          <div className="chart-head">
            <div className="chart-title">Latitudinal Profile</div>
            <ChartToolbar onExpand={latProfileFullscreen.expand} controls={latProfileControls} options={{ grid: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart
              data={latProfileData}
              layout={{ margin: { t: 10, r: 12, b: 32, l: 38 }, ...latProfileControls.layoutOverrides }}
              resetKey={latProfileControls.resetKey}
              height={220}
              downloadFilename={exportsEnabled.spatial ? `${parameter}-latitudinal-profile` : undefined}
            />
          </div>
        </div>
        <div className="chart-container">
          <div className="chart-head">
            <div className="chart-title">Longitudinal Profile</div>
            <ChartToolbar onExpand={lonProfileFullscreen.expand} controls={lonProfileControls} options={{ grid: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart
              data={lonProfileData}
              layout={{ margin: { t: 10, r: 12, b: 32, l: 38 }, ...lonProfileControls.layoutOverrides }}
              resetKey={lonProfileControls.resetKey}
              height={220}
              downloadFilename={exportsEnabled.spatial ? `${parameter}-longitudinal-profile` : undefined}
            />
          </div>
        </div>
      </div>
      {latProfileFullscreen.expanded && (
        <FullscreenOverlay title="Latitudinal Profile" onClose={latProfileFullscreen.collapse}>
          <PlotlyChart data={latProfileData} layout={latProfileControls.layoutOverrides} height={640} downloadFilename={exportsEnabled.spatial ? `${parameter}-latitudinal-profile` : undefined} />
        </FullscreenOverlay>
      )}
      {lonProfileFullscreen.expanded && (
        <FullscreenOverlay title="Longitudinal Profile" onClose={lonProfileFullscreen.collapse}>
          <PlotlyChart data={lonProfileData} layout={lonProfileControls.layoutOverrides} height={640} downloadFilename={exportsEnabled.spatial ? `${parameter}-longitudinal-profile` : undefined} />
        </FullscreenOverlay>
      )}
    </div>
  );

  const toolsPanel = (
    <>
      <div className="ctrl-heading" style={{ border: "none", margin: 0, padding: 0, marginBottom: "1.2rem" }}>
        🛰️ GIS Tools
      </div>

      <ToolGroup title="Selection Summary" icon="🔍">
        <p className="gis-caption" style={{ marginTop: 0, marginBottom: 0 }}>
          {gisPoints.length} station(s) in view.
          <br />
          {aoi ? (aoi.kind === "polygon" ? `Polygon AOI active (${aoi.ring.length} vertices).` : "Rectangular AOI active.") : "No Area of Interest selected."}
        </p>
      </ToolGroup>

      <ToolGroup title="Interpolation Settings" icon="🌐">
        <div className="ctrl-group">
          <label className="ctrl-label">Method</label>
          <select className="ctrl-select" value={method} onChange={(e) => setMethod(e.target.value as InterpolationMethod)}>
            <option value="idw">IDW</option>
            <option value="kriging">Kriging</option>
            <option value="nearest">Nearest Neighbour</option>
          </select>
          {method !== "idw" && (
            <div className="gis-caption" style={{ marginTop: "0.3rem", marginBottom: 0 }}>
              {method === "kriging"
                ? "Computed as IDW — kriging not available in this build."
                : "Real nearest-neighbour interpolation."}
            </div>
          )}
        </div>
        <div className="ctrl-group">
          <label className="ctrl-label">Resolution</label>
          <select className="ctrl-select" value={resolution} onChange={(e) => setResolution(parseInt(e.target.value, 10))}>
            <option value={20}>Low (fast)</option>
            <option value={40}>Medium</option>
            <option value={60}>High (slow)</option>
          </select>
        </div>
        <div className="ctrl-group">
          <label className="ctrl-label">Output</label>
          <select className="ctrl-select" value={output} onChange={(e) => setOutput(e.target.value as InterpolationOutput)}>
            <option value="contour">Contour</option>
            <option value="heatmap">Heatmap Raster</option>
            <option value="points-only">Points Only</option>
          </select>
        </div>
        <div className="ctrl-group">
          <label className="ctrl-label">Display Mode</label>
          <select className="ctrl-select" value={displayMode} onChange={(e) => setDisplayMode(e.target.value as InterpolationDisplayMode)}>
            <option value="continuous">Continuous</option>
            <option value="classified">Classified</option>
            <option value="custom">Custom Intervals</option>
          </select>
        </div>
        {displayMode === "classified" && (
          <div className="ctrl-group">
            <label className="ctrl-label">Classes</label>
            <input
              className="ctrl-input"
              type="number"
              min={3}
              max={10}
              value={classCount}
              onChange={(e) => setClassCount(Math.min(10, Math.max(3, Number(e.target.value))))}
            />
          </div>
        )}
        {displayMode === "custom" && (
          <div className="ctrl-group">
            <label className="ctrl-label">Breakpoints (comma-separated)</label>
            <input
              className="ctrl-input"
              type="text"
              placeholder={`e.g. ${min.toFixed(1)}, ${((min + max) / 2).toFixed(1)}, ${max.toFixed(1)}`}
              value={breakpointsText}
              onChange={(e) => setBreakpointsText(e.target.value)}
            />
          </div>
        )}
      </ToolGroup>

      <ToolGroup title="Color Ramp" icon="🎨">
        <ColorRampSelect value={ramp} onChange={setRamp} />
      </ToolGroup>

      <ToolGroup title="Base Map" icon="🗺️">
        <div className="basemap-btn-group">
          {BASE_MAPS.map((b) => (
            <button
              key={b.key}
              type="button"
              className={`map-tool-btn${baseMap === b.key ? " active" : ""}`}
              onClick={() => setBaseMap(b.key)}
            >
              {b.icon} {b.label}
            </button>
          ))}
        </div>
      </ToolGroup>

      <ToolGroup title="Opacity" icon="🎚️">
        <div className="ctrl-group">
          <label className="ctrl-label">Interpolation Overlay</label>
          <input type="range" style={{ width: "100%" }} min={0.2} max={1} step={0.05} value={interpolationOpacity} onChange={(e) => setInterpolationOpacity(Number(e.target.value))} />
        </div>
        <div className="ctrl-group">
          <label className="ctrl-label">Points Layer</label>
          <input type="range" style={{ width: "100%" }} min={0.2} max={1} step={0.05} value={pointsOpacity} onChange={(e) => setPointsOpacity(Number(e.target.value))} />
        </div>
      </ToolGroup>

      <ToolGroup title="Layer Controls" icon="🧾">
        <div className="ctrl-checkbox-row">
          <input type="checkbox" id="lyrPoints" checked={showPoints} onChange={(e) => setShowPoints(e.target.checked)} />
          <label htmlFor="lyrPoints">Observation Points (reference only)</label>
        </div>
        <div className="ctrl-checkbox-row">
          <input type="checkbox" id="lyrInterp" checked={showInterpolation} onChange={(e) => setShowInterpolation(e.target.checked)} />
          <label htmlFor="lyrInterp">Interpolation Overlay</label>
        </div>
        <div className="ctrl-checkbox-row">
          <input type="checkbox" id="lyrBoundary" checked={showBaseBoundary} onChange={(e) => setShowBaseBoundary(e.target.checked)} />
          <label htmlFor="lyrBoundary">{defaultBoundaryName ?? "Bangladesh Boundary"}</label>
        </div>
      </ToolGroup>

      <ToolGroup title="Custom Boundary" icon="📁" defaultOpen={false}>
        <button className="btn-reset-ctrl" style={{ marginTop: 0, justifyContent: "center", display: "flex", alignItems: "center", gap: "0.4rem" }} onClick={() => fileInputRef.current?.click()}>
          📁 Upload Shapefile (.zip)
        </button>
        <input ref={fileInputRef} type="file" accept=".zip,.shp" style={{ display: "none" }} onChange={handleUpload} />
        {uploadedName && (
          <div className="gis-caption" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.4rem" }}>
            <span>{uploadedName}</span>
            <button className="btn-clear-spatial" onClick={clearUpload}>
              Clear
            </button>
          </div>
        )}
        {!uploadedName && defaultBoundaryName && (
          <div className="gis-caption" style={{ marginBottom: 0 }}>
            Default: {defaultBoundaryName} (admin-managed)
          </div>
        )}
      </ToolGroup>

      <ToolGroup title="Value Scale" icon="📊">
        <div className="map-legend-title" style={{ marginBottom: "0.5rem" }}>
          {parameter}
        </div>
        {displayMode === "continuous" ? (
          <>
            <div className={`legend-gradient legend-gradient-${ramp.toLowerCase()}`} />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.68rem", color: "var(--text-muted)" }}>
              <span>{min.toFixed(1)}</span>
              <span>Mid</span>
              <span>{max.toFixed(1)}</span>
            </div>
          </>
        ) : (
          <div className="legend-swatches">
            {(displayMode === "classified" ? classBreaks : breakpoints).map((_, i, arr) => {
              const lo = i === 0 ? min : arr[i - 1];
              const hi = arr[i];
              return (
                <div key={i} className={`legend-swatch legend-gradient-${ramp.toLowerCase()}`} style={{ opacity: (i + 1) / (arr.length + 1) }}>
                  {lo.toFixed(1)}–{hi.toFixed(1)}
                </div>
              );
            })}
            <div className={`legend-swatch legend-gradient-${ramp.toLowerCase()}`} style={{ opacity: 1 }}>
              &gt;{(displayMode === "classified" ? classBreaks : breakpoints).slice(-1)[0]?.toFixed(1) ?? max.toFixed(1)}
            </div>
          </div>
        )}
      </ToolGroup>
    </>
  );

  return { mapPanel, toolsPanel };
}
