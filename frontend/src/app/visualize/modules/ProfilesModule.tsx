"use client";

import { useEffect, useState } from "react";
import PlotlyChart from "@/components/charts/PlotlyChart";
import ChartToolbar, { useChartControls } from "@/components/charts/ChartToolbar";
import { useFullscreenChart, FullscreenOverlay } from "@/components/charts/FullscreenChartWrapper";
import { ApiError } from "@/lib/api/client";
import { postProfiles } from "@/lib/api/visualize";
import { toVizFilterParams, type VizFilters } from "../useVizFilters";
import type { ProfilesResponse } from "@/lib/types/visualize";
import type { Data } from "plotly.js";

interface ProfilesModuleProps {
  filters: VizFilters;
  availableParameters: string[];
}

// Best-effort guesses at which detected variable is Temperature/Salinity,
// for the T-S diagram's default selection only — never used to silently
// substitute a variable the user didn't pick; the two <select> dropdowns
// below always show every real option and the user can change either.
function guessVariable(vars: string[], keywords: string[]): string {
  const lower = vars.map((v) => v.toLowerCase());
  for (const kw of keywords) {
    const idx = lower.findIndex((v) => v.includes(kw));
    if (idx !== -1) return vars[idx];
  }
  return vars[0] ?? "";
}

export default function ProfilesModule({ filters, availableParameters }: ProfilesModuleProps) {
  const vars = availableParameters.length > 0 ? availableParameters : [filters.parameter].filter(Boolean);

  const [profileVar, setProfileVar] = useState(vars[0] ?? filters.parameter);
  const [tempVar, setTempVar] = useState(() => guessVariable(vars, ["temp", "temperature"]));
  const [salVar, setSalVar] = useState(() => guessVariable(vars, ["sal", "salinity"]));

  const [profileData, setProfileData] = useState<ProfilesResponse | null>(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [tsData, setTsData] = useState<ProfilesResponse | null>(null);
  const [tsLoading, setTsLoading] = useState(true);
  const [tsError, setTsError] = useState<string | null>(null);

  const profileFullscreen = useFullscreenChart();
  const tsFullscreen = useFullscreenChart();
  const profileControls = useChartControls({ legend: true, grid: true, resetView: true });
  const tsControls = useChartControls({ legend: true, grid: true, resetView: true });

  useEffect(() => {
    if (!profileVar) {
      setProfileLoading(false);
      return;
    }
    const controller = new AbortController();
    setProfileLoading(true);
    setProfileError(null);
    postProfiles({ parameter: profileVar, ...toVizFilterParams(filters) }, { signal: controller.signal })
      .then((res) => {
        setProfileData(res);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setProfileError(err instanceof ApiError ? err.message : "Failed to load profile data.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setProfileLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [profileVar, filters]);

  useEffect(() => {
    if (!tempVar || !salVar) {
      setTsLoading(false);
      return;
    }
    const controller = new AbortController();
    setTsLoading(true);
    setTsError(null);
    postProfiles(
      { temperature_parameter: tempVar, salinity_parameter: salVar, ...toVizFilterParams(filters) },
      { signal: controller.signal }
    )
      .then((res) => {
        setTsData(res);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setTsError(err instanceof ApiError ? err.message : "Failed to load T-S diagram data.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setTsLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [tempVar, salVar, filters]);

  const hasDepthData = profileData?.has_depth_data ?? true;
  const depthConvention = profileData?.depth_convention ?? tsData?.depth_convention ?? null;
  // Depth is always stored/returned positive-down by ingestion's own
  // convention detection UNLESS the source data was genuinely negative-up
  // (see csv_parser.py/netcdf_parser.py's depth_convention derivation) —
  // never flipped here; only the Plotly axis direction responds to it, so
  // "deeper on screen = deeper in the water column" always holds
  // regardless of which sign the source file actually used.
  const depthAxisReversed = depthConvention !== "assumed_negative_up";

  const profileFilename = `${profileVar}-depth-profile`;
  const tsFilename = "ts-diagram";

  const profiles = profileData?.profiles ?? [];
  const profileTraces: Data[] = profiles.map((p, i) => ({
    x: p.points.map((pt) => pt.value),
    y: p.points.map((pt) => pt.depth_m),
    mode: "lines+markers",
    type: "scatter",
    name: p.station ?? (p.time ? `Cast ${p.time}` : `Profile ${i + 1}`),
    marker: { size: 4 },
    line: { width: 2 },
    hovertemplate:
      `${p.station ? `Station: ${p.station}<br>` : ""}` +
      `${p.time ? `Date: ${p.time}<br>` : ""}` +
      "Depth: %{y:.1f} m<br>" +
      `${profileVar}: %{x:.3f}<extra></extra>`,
  }));

  const tsPairs = tsData?.ts_pairs ?? [];
  const hasDepthColoring = tsPairs.some((p) => p.depth_m !== null);
  const tsTrace: Data = {
    x: tsPairs.map((p) => p.salinity),
    y: tsPairs.map((p) => p.temperature),
    mode: "markers",
    type: "scatter",
    name: "T-S observations",
    marker: hasDepthColoring
      ? {
          size: 6,
          color: tsPairs.map((p) => p.depth_m ?? 0),
          colorscale: "Viridis",
          reversescale: true,
          showscale: true,
          colorbar: { title: { text: "Depth (m)" } },
        }
      : { size: 6, color: "var(--accent, #2a9d8f)" },
    customdata: tsPairs.map((p) => [p.station ?? "—", p.depth_m, p.lat, p.lon, p.time ?? "—"]) as unknown as string[],
    hovertemplate:
      "Salinity: %{x:.3f}<br>Temperature: %{y:.3f}<br>" +
      "Station: %{customdata[0]}<br>Depth: %{customdata[1]} m<br>" +
      "Lat/Lon: %{customdata[2]}, %{customdata[3]}<br>Date: %{customdata[4]}<extra></extra>",
  };

  const profileChart = (height: number) => (
    <PlotlyChart
      height={height}
      data={profileTraces}
      layout={{
        ...profileControls.layoutOverrides,
        // Oceanographic vertical-profile convention (matches standard
        // published CTD figures): the value axis runs along the TOP of
        // the plot, with depth increasing downward directly beneath it —
        // not the default bottom-axis layout used by every other chart
        // in this module, since a profile is read top-to-bottom like the
        // water column itself, not left-to-right like a time series.
        // Spread AFTER layoutOverrides (unlike every other module here)
        // and merged field-by-field, not replaced wholesale — Plotly's
        // `Layout.xaxis`/`yaxis` are plain objects, so `{...a, ...b}`
        // replaces the whole axis config rather than merging it, and
        // profileControls.layoutOverrides.xaxis/yaxis (grid-toggle
        // state) would otherwise silently wipe out `side`/`autorange`.
        xaxis: {
          ...profileControls.layoutOverrides.xaxis,
          title: { text: profileVar },
          side: "top",
        },
        yaxis: {
          ...profileControls.layoutOverrides.yaxis,
          title: { text: "Depth (m)" },
          autorange: depthAxisReversed ? "reversed" : true,
        },
      }}
      resetKey={profileControls.resetKey}
      downloadFilename={profileFilename}
    />
  );

  const tsChart = (height: number) => (
    <PlotlyChart
      height={height}
      data={[tsTrace]}
      layout={{
        ...tsControls.layoutOverrides,
        xaxis: { ...tsControls.layoutOverrides.xaxis, title: { text: "Salinity (PSU)" } },
        yaxis: { ...tsControls.layoutOverrides.yaxis, title: { text: "Temperature (°C)" } },
      }}
      resetKey={tsControls.resetKey}
      downloadFilename={tsFilename}
    />
  );

  if (!hasDepthData) {
    return (
      <div className="empty-state">
        <div className="es-icon">🌊</div>
        <p>This dataset has no detected depth/vertical coordinate — Oceanographic Profiles don&apos;t apply here.</p>
      </div>
    );
  }

  return (
    <div>
      <div className="chart-container" style={{ marginBottom: "1.2rem" }}>
        <div className="chart-head">
          <div>
            <div className="chart-title">Vertical Profile — {profileVar || "Select a variable"}</div>
            <div className="chart-subtitle">Depth increases downward · one line per station/cast</div>
          </div>
          <ChartToolbar onExpand={profileFullscreen.expand} controls={profileControls} options={{ legend: true, grid: true, resetView: true }} />
        </div>
        <div className="gis-ctrl-row" style={{ padding: "0.9rem 1.2rem 0", margin: 0 }}>
          <span className="gis-label">Variable:</span>
          <select className="gis-select" value={profileVar} onChange={(e) => setProfileVar(e.target.value)}>
            {vars.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          {!profileLoading && profiles.length > 0 && (
            <span className="gis-label" style={{ marginLeft: "auto" }}>
              {profiles.length} profile{profiles.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <div className="chart-body">
          {profileError ? (
            <div className="empty-state">
              <div className="es-icon">⚠️</div>
              <p>{profileError}</p>
            </div>
          ) : !profileLoading && profiles.length === 0 ? (
            <div className="empty-state">
              <div className="es-icon">📭</div>
              <p>No valid depth observations for the current filters.</p>
            </div>
          ) : (
            profileChart(380)
          )}
        </div>
      </div>
      {profileFullscreen.expanded && (
        <FullscreenOverlay title={`${profileVar} — Vertical Profile`} onClose={profileFullscreen.collapse}>
          {profileChart(640)}
        </FullscreenOverlay>
      )}

      <div className="chart-container">
        <div className="chart-head">
          <div>
            <div className="chart-title">Temperature–Salinity (T-S) Diagram</div>
            <div className="chart-subtitle">Paired observations from the same depth/cast</div>
          </div>
          <ChartToolbar onExpand={tsFullscreen.expand} controls={tsControls} options={{ legend: true, grid: true, resetView: true }} />
        </div>
        <div className="gis-ctrl-row" style={{ padding: "0.9rem 1.2rem 0", margin: 0 }}>
          <span className="gis-label">Temperature:</span>
          <select className="gis-select" value={tempVar} onChange={(e) => setTempVar(e.target.value)}>
            {vars.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          <span className="gis-label">Salinity:</span>
          <select className="gis-select" value={salVar} onChange={(e) => setSalVar(e.target.value)}>
            {vars.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          {!tsLoading && tsPairs.length > 0 && (
            <span className="gis-label" style={{ marginLeft: "auto" }}>
              N = {tsPairs.length} paired observations
            </span>
          )}
        </div>
        <div className="chart-body">
          {tsError ? (
            <div className="empty-state">
              <div className="es-icon">⚠️</div>
              <p>{tsError}</p>
            </div>
          ) : !tsLoading && tsPairs.length === 0 ? (
            <div className="empty-state">
              <div className="es-icon">📭</div>
              <p>No paired Temperature/Salinity observations for the current filters.</p>
            </div>
          ) : (
            tsChart(380)
          )}
        </div>
      </div>
      {tsFullscreen.expanded && (
        <FullscreenOverlay title="Temperature–Salinity Diagram" onClose={tsFullscreen.collapse}>
          {tsChart(640)}
        </FullscreenOverlay>
      )}

      <p className="gis-caption" style={{ marginTop: "0.9rem", color: "var(--text-muted)" }}>
        Density contours/isopycnals are not shown — no validated seawater-thermodynamics implementation
        (TEOS-10/UNESCO EOS-80) is available in this system yet; this is a documented limitation, not an
        approximation.
      </p>
    </div>
  );
}
