"use client";

import { useEffect, useMemo, useState } from "react";
import PlotlyChart from "@/components/charts/PlotlyChart";
import ChartToolbar, { useChartControls } from "@/components/charts/ChartToolbar";
import { useFullscreenChart, FullscreenOverlay } from "@/components/charts/FullscreenChartWrapper";
import type { ColorRampName } from "@/lib/geo/colorRamp";
import { ApiError } from "@/lib/api/client";
import { getComparisonJob, postComparison } from "@/lib/api/visualize";
import { toVizFilterParams, type VizFilters } from "../useVizFilters";
import { useVizExportSettings } from "../useVizExportSettings";
import type { ComparisonResponse } from "@/lib/types/visualize";
import type { Data } from "plotly.js";

interface ComparisonModuleProps {
  filters: VizFilters;
  availableParameters: string[];
}

// Heavy requests dispatch to a Celery job (Visualize Performance plan,
// Phase 4) instead of computing in-process — same poll loop
// SpatialMappingModule.tsx already uses for its own job dispatch.
const POLL_INTERVAL_MS = 1500;

// Human-readable labels for ScatterResult.pairing_method — a stable,
// machine-readable value from the backend (e.g. "exact_date_match")
// mapped to display text here, same pattern as SpatialMappingModule's
// BASE_MAPS/interpolation-method labels.
const PAIRING_METHOD_LABELS: Record<string, string> = {
  exact_date_match: "Exact date match",
  // Visualize Module audit fix (Multivariable — Non-Temporal Datasets):
  // the backend's fallback pairing key for a dataset with no time
  // dimension — observations are paired by matching (lat, lon) instead
  // of matching date.
  lat_lon_match: "Matched by location (lat/lon)",
};

export default function ComparisonModule({ filters, availableParameters }: ComparisonModuleProps) {
  const vars = availableParameters.length >= 2 ? availableParameters : [filters.parameter];
  const [xVar, setXVar] = useState(vars[0] ?? filters.parameter);
  const [yVar, setYVar] = useState(vars[1] ?? vars[0] ?? filters.parameter);
  const [corrVars, setCorrVars] = useState<string[]>(vars.slice(0, 3));
  const [corrRamp, setCorrRamp] = useState<ColorRampName>("RdBu");

  const [scatterData2, setScatterData2] = useState<ComparisonResponse | null>(null);
  const [corrData, setCorrData] = useState<ComparisonResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const exportsEnabled = useVizExportSettings();

  const scatterFullscreen = useFullscreenChart();
  const tsFullscreen = useFullscreenChart();
  const corrFullscreen = useFullscreenChart();

  const scatterControls = useChartControls({ legend: true, grid: true, resetView: true });
  const tsControls = useChartControls({ legend: true, grid: true, resetView: true });
  const corrControls = useChartControls({ resetView: true });

  useEffect(() => {
    const controller = new AbortController();
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    setLoading(true);
    setError(null);

    async function run() {
      try {
        const res = await postComparison(
          { parameters: [xVar, yVar], ...toVizFilterParams(filters) },
          { signal: controller.signal }
        );

        if (res.status === "complete") {
          if (res.result) setScatterData2(res.result);
          setLoading(false);
          return;
        }

        const jobId = res.job_id;
        if (!jobId) return;

        async function poll() {
          const job = await getComparisonJob(jobId!);
          if (controller.signal.aborted) return;
          if (job.status === "complete") {
            if (job.result) setScatterData2(job.result);
            setLoading(false);
          } else if (job.status === "failed") {
            setError(job.error_message ?? "Failed to load comparison data.");
            setLoading(false);
          } else {
            pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
          }
        }
        await poll();
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof ApiError ? err.message : "Failed to load comparison data.");
        setLoading(false);
      }
    }

    run();
    return () => {
      controller.abort();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [xVar, yVar, filters]);

  useEffect(() => {
    if (corrVars.length < 2) {
      setCorrData(null);
      return;
    }
    const controller = new AbortController();
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    async function run() {
      try {
        const res = await postComparison(
          { parameters: corrVars, ...toVizFilterParams(filters) },
          { signal: controller.signal }
        );

        if (res.status === "complete") {
          if (res.result) setCorrData(res.result);
          return;
        }

        const jobId = res.job_id;
        if (!jobId) return;

        async function poll() {
          const job = await getComparisonJob(jobId!);
          if (controller.signal.aborted) return;
          if (job.status === "complete") {
            if (job.result) setCorrData(job.result);
          } else if (job.status === "failed") {
            // Matrix errors were already silently swallowed before this
            // phase — kept as-is (the scatter effect above is the one
            // that surfaces a visible error state).
          } else {
            pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
          }
        }
        await poll();
      } catch {
        // Matches this effect's pre-existing behavior — errors here were
        // always silently swallowed (see the scatter effect above for
        // the one that surfaces a visible error).
      }
    }

    run();
    return () => {
      controller.abort();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [corrVars, filters]);

  function toggleCorrVar(v: string) {
    setCorrVars((prev) => (prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v]));
  }

  const r = scatterData2?.scatter.r ?? 0;
  const rSquared = scatterData2?.scatter.regression.r_squared ?? 0;
  const pairedN = scatterData2?.scatter.n ?? 0;
  const pairingMethodLabel = PAIRING_METHOD_LABELS[scatterData2?.scatter.pairing_method ?? ""] ?? scatterData2?.scatter.pairing_method ?? "";
  const xVals = scatterData2?.scatter.x ?? [];
  const yVals = scatterData2?.scatter.y ?? [];

  const regressionLine = useMemo(() => {
    const regression = scatterData2?.scatter.regression ?? { slope: 0, intercept: 0 };
    const sortedX = [...(scatterData2?.scatter.x ?? [])].sort((a, b) => a - b);
    return { x: sortedX, y: sortedX.map((x) => regression.intercept + regression.slope * x) };
  }, [scatterData2]);

  const scatterData: Data[] = [
    { x: xVals, y: yVals, mode: "markers", type: "scatter", name: `${xVar} vs ${yVar}` } as Data,
    {
      x: regressionLine.x,
      y: regressionLine.y,
      mode: "lines",
      type: "scatter",
      name: `Regression (r=${r.toFixed(2)})`,
      line: { dash: "dash" },
    } as Data,
  ];

  const xSeries = scatterData2?.series_by_parameter[xVar] ?? [];
  const ySeries = scatterData2?.series_by_parameter[yVar] ?? [];
  const tsData: Data[] = [
    { x: xSeries.map((p) => p.date), y: xSeries.map((p) => p.value), type: "scatter", mode: "lines", name: xVar, yaxis: "y" } as Data,
    { x: ySeries.map((p) => p.date), y: ySeries.map((p) => p.value), type: "scatter", mode: "lines", name: yVar, yaxis: "y2" } as Data,
  ];

  const corrMatrix = corrData?.correlation_matrix.matrix ?? [];
  const corrNMatrix = corrData?.correlation_matrix.n ?? [];
  const corrParameters = corrData?.correlation_matrix.parameters ?? corrVars;

  const scatterFilename = exportsEnabled.comparison ? `${xVar}-vs-${yVar}-scatter` : undefined;
  const tsFilename = exportsEnabled.comparison ? `${xVar}-${yVar}-time-series` : undefined;
  const corrFilename = exportsEnabled.comparison ? "correlation-matrix" : undefined;

  const scatterChart = (height: number) => (
    <PlotlyChart
      height={height}
      data={scatterData}
      layout={{ xaxis: { title: { text: xVar } }, yaxis: { title: { text: yVar } }, ...scatterControls.layoutOverrides }}
      resetKey={scatterControls.resetKey}
      downloadFilename={scatterFilename}
    />
  );
  const tsChart = (height: number) => (
    <PlotlyChart
      height={height}
      data={tsData}
      layout={{
        yaxis: { title: { text: xVar } },
        yaxis2: { title: { text: yVar }, overlaying: "y", side: "right" },
        ...tsControls.layoutOverrides,
      }}
      resetKey={tsControls.resetKey}
      downloadFilename={tsFilename}
    />
  );
  const corrChart = (height: number) => (
    <PlotlyChart
      height={height}
      resetKey={corrControls.resetKey}
      downloadFilename={corrFilename}
      data={[
        {
          z: corrMatrix,
          x: corrParameters,
          y: corrParameters,
          type: "heatmap",
          colorscale: corrRamp,
          zmin: -1,
          zmax: 1,
          text: corrMatrix.map((row) => row.map((v) => v.toFixed(2))) as unknown as string[],
          texttemplate: "%{text}",
          customdata: corrNMatrix as unknown as string[],
          hovertemplate: "%{x} × %{y}<br>r = %{z:.3f}<br>n = %{customdata} paired obs. (pairwise-complete)<extra></extra>",
          colorbar: { title: { text: "Pearson r" } },
        } as Data,
      ]}
      layout={{ yaxis: { automargin: true }, xaxis: { automargin: true } }}
    />
  );

  if (error) {
    return (
      <div className="empty-state">
        <div className="es-icon">⚠️</div>
        <p>{error}</p>
      </div>
    );
  }
  // Visualize Module audit fix (Multivariable — Non-Temporal Datasets):
  // previously this whole module was blocked for any dataset with no
  // time dimension. Scatter/regression and the correlation matrix now
  // work via the backend's (lat, lon) pairing fallback — only the
  // "Multi-Variable Time Series" sub-chart genuinely has no meaning
  // without a date axis, so only that one section is conditionally
  // hidden below (same principle as Statistics leaving its own
  // date-bucketed sections empty rather than fabricated).
  const hasTemporalData = scatterData2?.has_temporal_data ?? true;

  return (
    <div>
      <div className="chart-container" style={{ marginBottom: "1.2rem" }}>
        <div className="chart-head">
          <div>
            <div className="chart-title">Scatter Plot &amp; Regression — Axis Assignment</div>
            <div className="chart-subtitle">Assign any two variables to X and Y to explore relationships</div>
          </div>
          <ChartToolbar onExpand={scatterFullscreen.expand} controls={scatterControls} options={{ legend: true, grid: true, resetView: true }} />
        </div>
        <div className="gis-ctrl-row" style={{ padding: "0.9rem 1.2rem 0", margin: 0 }}>
          <span className="gis-label">X-Axis:</span>
          <select className="gis-select" value={xVar} onChange={(e) => setXVar(e.target.value)}>
            {vars.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          <span className="gis-label">Y-Axis:</span>
          <select className="gis-select" value={yVar} onChange={(e) => setYVar(e.target.value)}>
            {vars.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          <span className="gis-label" style={{ marginLeft: "auto" }}>
            Pearson r = <b style={{ color: "var(--accent)" }}>{loading ? "…" : r.toFixed(3)}</b>
            {!loading && <> (R&sup2; = {rSquared.toFixed(3)})</>}
          </span>
        </div>
        {!loading && (
          <div
            className="gis-caption"
            style={{ padding: "0.35rem 1.2rem 0", marginTop: 0, marginBottom: 0, color: "var(--text-muted)" }}
          >
            N = {pairedN} paired observation{pairedN === 1 ? "" : "s"} &middot; Pairing: {pairingMethodLabel}
          </div>
        )}
        <div className="chart-body">{scatterChart(360)}</div>
      </div>
      {scatterFullscreen.expanded && (
        <FullscreenOverlay title={`${xVar} vs ${yVar} — Scatter & Regression`} onClose={scatterFullscreen.collapse}>
          {scatterChart(640)}
        </FullscreenOverlay>
      )}

      {hasTemporalData ? (
        <>
          <div className="chart-container" style={{ marginBottom: "1.2rem" }}>
            <div className="chart-head">
              <div>
                <div className="chart-title">Multi-Variable Time Series</div>
                <div className="chart-subtitle">
                  {xVar} &amp; {yVar} overlaid on dual axes — respects active sidebar filters
                </div>
              </div>
              <ChartToolbar onExpand={tsFullscreen.expand} controls={tsControls} options={{ legend: true, grid: true, resetView: true }} />
            </div>
            <div className="chart-body">{tsChart(300)}</div>
          </div>
          {tsFullscreen.expanded && (
            <FullscreenOverlay title={`${xVar} & ${yVar} — Multi-Variable Time Series`} onClose={tsFullscreen.collapse}>
              {tsChart(640)}
            </FullscreenOverlay>
          )}
        </>
      ) : (
        <div className="chart-container" style={{ marginBottom: "1.2rem", display: "flex", alignItems: "center", justifyContent: "center", minHeight: 120 }}>
          <p className="gis-caption" style={{ margin: 0, textAlign: "center" }}>
            This dataset has no time dimension — the time-series overlay doesn&apos;t apply. Scatter,
            regression, and the correlation matrix above/below still reflect real paired data, matched by
            location instead of date.
          </p>
        </div>
      )}

      <div className="chart-container">
        <div className="chart-head">
          <div>
            <div className="chart-title">Correlation Matrix</div>
            <div className="chart-subtitle">Select 2+ variables to compare — reflects active filters</div>
          </div>
          <ChartToolbar
            onExpand={corrFullscreen.expand}
            controls={corrControls}
            options={{ resetView: true, palette: { value: corrRamp, onChange: setCorrRamp } }}
          />
        </div>
        <div style={{ padding: "0.9rem 1.2rem 0" }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
            {vars.map((v) => (
              <label key={v} className={`chip-checkbox${corrVars.includes(v) ? " active" : ""}`}>
                <input type="checkbox" checked={corrVars.includes(v)} onChange={() => toggleCorrVar(v)} />
                {v}
              </label>
            ))}
          </div>
        </div>
        <div className="chart-body">
          {corrVars.length < 2 ? (
            <div className="empty-state">
              <div className="es-icon">📐</div>
              <p>Select at least 2 variables above to generate a correlation matrix.</p>
            </div>
          ) : (
            corrChart(340)
          )}
        </div>
      </div>
      {corrFullscreen.expanded && corrVars.length >= 2 && (
        <FullscreenOverlay title="Correlation Matrix" onClose={corrFullscreen.collapse}>
          {corrChart(640)}
        </FullscreenOverlay>
      )}
    </div>
  );
}
