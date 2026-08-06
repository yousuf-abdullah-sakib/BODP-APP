"use client";

import { useEffect, useState } from "react";
import PlotlyChart from "@/components/charts/PlotlyChart";
import ChartToolbar, { useChartControls } from "@/components/charts/ChartToolbar";
import { useFullscreenChart, FullscreenOverlay } from "@/components/charts/FullscreenChartWrapper";
import { ApiError } from "@/lib/api/client";
import { postTimeseries } from "@/lib/api/visualize";
import { toVizFilterParams, type VizFilters } from "../useVizFilters";
import { useVizExportSettings } from "../useVizExportSettings";
import type { TimeSeriesResponse } from "@/lib/types/visualize";
import type { Data } from "plotly.js";

type ChartType = "line" | "bar" | "area" | "scatter" | "histogram" | "box";

const CHART_TYPES: { key: ChartType; label: string }[] = [
  { key: "line", label: "📈 Line" },
  { key: "bar", label: "📊 Bar" },
  { key: "area", label: "🏔 Area" },
  { key: "scatter", label: "⬤ Scatter" },
  { key: "histogram", label: "▦ Histogram" },
  { key: "box", label: "▭ Box Plot" },
];

const DISTRIBUTION_COLOR = "#7c3aed";
const ANOMALY_POS_COLOR = "#dc2626";
const ANOMALY_NEG_COLOR = "#0891b2";
const SEASONAL_COLOR = "#ea580c";
const CLIMATOLOGY_COLOR = "#0891b2";

export default function TemporalModule({
  filters,
  showTrend,
  showMA,
}: {
  filters: VizFilters;
  showTrend: boolean;
  showMA: boolean;
}) {
  const [chartType, setChartType] = useState<ChartType>("line");
  const [data, setData] = useState<TimeSeriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const exportsEnabled = useVizExportSettings();

  const mainFullscreen = useFullscreenChart();
  const seasonalFullscreen = useFullscreenChart();
  const climatologyFullscreen = useFullscreenChart();
  const rocFullscreen = useFullscreenChart();
  const anomalyFullscreen = useFullscreenChart();

  const mainControls = useChartControls({ legend: true, grid: true, resetView: true });
  const seasonalControls = useChartControls({ grid: true, resetView: true });
  const climatologyControls = useChartControls({ grid: true, resetView: true });
  const rocControls = useChartControls({ grid: true, resetView: true });
  const anomalyControls = useChartControls({ grid: true, resetView: true });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    postTimeseries({ parameter: filters.parameter, ...toVizFilterParams(filters) })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load time series data.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters]);

  if (error) {
    return (
      <div className="empty-state">
        <div className="es-icon">⚠️</div>
        <p>{error}</p>
      </div>
    );
  }
  if (loading || !data) {
    return (
      <div className="empty-state">
        <div className="es-icon">⏳</div>
        <p>Loading time series…</p>
      </div>
    );
  }

  const { series, stats, moving_average, trend_line, seasonal, climatology, rate_of_change, anomaly } = data;
  const labels = series.map((p) => p.date);
  const values = series.map((p) => p.value);

  const downloadFilename = exportsEnabled.temporal ? `${filters.parameter}-time-series` : undefined;

  const mainData: Data[] = (() => {
    const traces: Data[] = [];
    const baseTrace: Partial<Data> = { x: labels, y: values, name: filters.parameter };

    if (chartType === "line") traces.push({ ...baseTrace, type: "scatter", mode: "lines+markers", line: { width: 2 } } as Data);
    else if (chartType === "bar") traces.push({ ...baseTrace, type: "bar" } as Data);
    else if (chartType === "area") traces.push({ ...baseTrace, type: "scatter", mode: "lines", fill: "tozeroy" } as Data);
    else if (chartType === "scatter") traces.push({ ...baseTrace, type: "scatter", mode: "markers" } as Data);
    else if (chartType === "histogram") traces.push({ x: values, type: "histogram", name: filters.parameter, marker: { color: DISTRIBUTION_COLOR } } as Data);
    else if (chartType === "box") traces.push({ y: values, type: "box", name: filters.parameter } as Data);

    if (chartType !== "histogram" && chartType !== "box") {
      if (showMA) {
        traces.push({ x: labels, y: moving_average, type: "scatter", mode: "lines", name: "Moving Avg (3mo)", line: { dash: "dot", width: 1.5 } } as Data);
      }
      if (showTrend) {
        traces.push({ x: labels, y: trend_line, type: "scatter", mode: "lines", name: "Trend", line: { dash: "dash", width: 1.5 } } as Data);
      }
    }

    return traces;
  })();

  const seasonalData: Data[] = [
    { x: seasonal.map((s) => s.label), y: seasonal.map((s) => s.value), type: "bar", marker: { color: SEASONAL_COLOR } } as Data,
  ];
  const climatologyData: Data[] = [
    {
      x: climatology.map((m) => m.month),
      y: climatology.map((m) => m.value),
      type: "scatter",
      mode: "lines+markers",
      line: { color: CLIMATOLOGY_COLOR },
      marker: { color: CLIMATOLOGY_COLOR },
    } as Data,
  ];
  const rocData: Data[] = [
    {
      x: rate_of_change.map((r) => r.date),
      y: rate_of_change.map((r) => r.delta),
      type: "bar",
      marker: { color: rate_of_change.map((r) => (r.delta >= 0 ? ANOMALY_POS_COLOR : ANOMALY_NEG_COLOR)) },
    } as Data,
  ];
  const anomalyData: Data[] = [
    {
      x: anomaly.map((a) => a.date),
      y: anomaly.map((a) => a.anomaly),
      type: "bar",
      marker: { color: anomaly.map((a) => (a.anomaly >= 0 ? ANOMALY_POS_COLOR : ANOMALY_NEG_COLOR)) },
    } as Data,
  ];

  return (
    <div>
      <div className="stat-strip">
        <div className="stat-chip">
          <div className="stat-chip-num">{stats.mean.toFixed(2)}</div>
          <div className="stat-chip-lbl">Mean Value</div>
        </div>
        <div className="stat-chip">
          <div className="stat-chip-num">{stats.max.toFixed(2)}</div>
          <div className="stat-chip-lbl">Maximum</div>
        </div>
        <div className="stat-chip">
          <div className="stat-chip-num">{stats.min.toFixed(2)}</div>
          <div className="stat-chip-lbl">Minimum</div>
        </div>
        <div className="stat-chip">
          <div className="stat-chip-num">{stats.trend_per_year >= 0 ? "+" : ""}{stats.trend_per_year.toFixed(3)}</div>
          <div className="stat-chip-lbl">Trend/Year</div>
        </div>
      </div>

      <div className="chart-type-group">
        {CHART_TYPES.map((t) => (
          <button
            key={t.key}
            className={`ctype-btn${chartType === t.key ? " active" : ""}`}
            onClick={() => setChartType(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="chart-container">
        <div className="chart-head">
          <div>
            <div className="chart-title">{filters.parameter} — Time Series</div>
            <div className="chart-subtitle">
              {filters.station || "All stations"} · {series[0]?.date ?? ""} – {series[series.length - 1]?.date ?? ""}
            </div>
          </div>
          <ChartToolbar onExpand={mainFullscreen.expand} controls={mainControls} options={{ legend: true, grid: true, resetView: true }} />
        </div>
        <div className="chart-body">
          <PlotlyChart data={mainData} layout={mainControls.layoutOverrides} resetKey={mainControls.resetKey} height={360} downloadFilename={downloadFilename} />
        </div>
      </div>
      {mainFullscreen.expanded && (
        <FullscreenOverlay title={`${filters.parameter} — Time Series`} onClose={mainFullscreen.collapse}>
          <PlotlyChart data={mainData} layout={mainControls.layoutOverrides} height={640} downloadFilename={downloadFilename} />
        </FullscreenOverlay>
      )}

      <div className="analysis-card">
        <div style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em", color: "var(--accent)", marginBottom: "0.7rem" }}>
          Statistical Summary
        </div>
        <div className="analysis-row">
          <div className="analysis-item">
            <div className="analysis-val">{stats.mean.toFixed(2)}</div>
            <div className="analysis-lbl">Mean</div>
          </div>
          <div className="analysis-item">
            <div className="analysis-val">{stats.median.toFixed(2)}</div>
            <div className="analysis-lbl">Median</div>
          </div>
          <div className="analysis-item">
            <div className="analysis-val">{stats.std.toFixed(2)}</div>
            <div className="analysis-lbl">Std Dev</div>
          </div>
          <div className="analysis-item">
            <div className="analysis-val">{stats.min.toFixed(2)}</div>
            <div className="analysis-lbl">Min</div>
          </div>
          <div className="analysis-item">
            <div className="analysis-val">{stats.max.toFixed(2)}</div>
            <div className="analysis-lbl">Max</div>
          </div>
          <div className="analysis-item">
            <div className="analysis-val">{values.length}</div>
            <div className="analysis-lbl">Count</div>
          </div>
        </div>
      </div>

      <div className="chart-row-2">
        <div className="chart-container">
          <div className="chart-head">
            <div className="chart-title">Seasonal Pattern</div>
            <ChartToolbar onExpand={seasonalFullscreen.expand} controls={seasonalControls} options={{ grid: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart data={seasonalData} layout={seasonalControls.layoutOverrides} resetKey={seasonalControls.resetKey} height={260} downloadFilename={downloadFilename ? `${filters.parameter}-seasonal-pattern` : undefined} />
          </div>
        </div>
        <div className="chart-container">
          <div className="chart-head">
            <div className="chart-title">Monthly Climatology</div>
            <ChartToolbar onExpand={climatologyFullscreen.expand} controls={climatologyControls} options={{ grid: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart data={climatologyData} layout={climatologyControls.layoutOverrides} resetKey={climatologyControls.resetKey} height={260} downloadFilename={downloadFilename ? `${filters.parameter}-monthly-climatology` : undefined} />
          </div>
        </div>
      </div>
      {seasonalFullscreen.expanded && (
        <FullscreenOverlay title="Seasonal Pattern" onClose={seasonalFullscreen.collapse}>
          <PlotlyChart data={seasonalData} layout={seasonalControls.layoutOverrides} height={640} downloadFilename={downloadFilename ? `${filters.parameter}-seasonal-pattern` : undefined} />
        </FullscreenOverlay>
      )}
      {climatologyFullscreen.expanded && (
        <FullscreenOverlay title="Monthly Climatology" onClose={climatologyFullscreen.collapse}>
          <PlotlyChart data={climatologyData} layout={climatologyControls.layoutOverrides} height={640} downloadFilename={downloadFilename ? `${filters.parameter}-monthly-climatology` : undefined} />
        </FullscreenOverlay>
      )}

      <div className="season-row">
        {[
          { icon: "🌸", name: "Pre-Monsoon", val: seasonal[0]?.value, unit: "Mar–May avg" },
          { icon: "🌧️", name: "Monsoon", val: seasonal[1]?.value, unit: "Jun–Sep avg" },
          { icon: "🍂", name: "Post-Monsoon", val: seasonal[2]?.value, unit: "Oct–Nov avg" },
          { icon: "❄️", name: "Winter", val: seasonal[3]?.value, unit: "Dec–Feb avg" },
        ].map((s) => (
          <div className="season-card" key={s.name}>
            <div className="season-icon">{s.icon}</div>
            <div className="season-name">{s.name}</div>
            <div className="season-val">{s.val?.toFixed(2) ?? "—"}</div>
            <div className="season-unit">{s.unit}</div>
          </div>
        ))}
      </div>

      <div className="chart-row-2">
        <div className="chart-container">
          <div className="chart-head">
            <div>
              <div className="chart-title">Rate of Change</div>
              <div className="chart-subtitle">Month-over-month delta</div>
            </div>
            <ChartToolbar onExpand={rocFullscreen.expand} controls={rocControls} options={{ grid: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart data={rocData} layout={rocControls.layoutOverrides} resetKey={rocControls.resetKey} height={260} downloadFilename={downloadFilename ? `${filters.parameter}-rate-of-change` : undefined} />
          </div>
        </div>
        <div className="chart-container">
          <div className="chart-head">
            <div>
              <div className="chart-title">Anomaly from Mean</div>
              <div className="chart-subtitle">Deviation from period average</div>
            </div>
            <ChartToolbar onExpand={anomalyFullscreen.expand} controls={anomalyControls} options={{ grid: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart data={anomalyData} layout={anomalyControls.layoutOverrides} resetKey={anomalyControls.resetKey} height={260} downloadFilename={downloadFilename ? `${filters.parameter}-anomaly` : undefined} />
          </div>
        </div>
      </div>
      {rocFullscreen.expanded && (
        <FullscreenOverlay title="Rate of Change" onClose={rocFullscreen.collapse}>
          <PlotlyChart data={rocData} layout={rocControls.layoutOverrides} height={640} downloadFilename={downloadFilename ? `${filters.parameter}-rate-of-change` : undefined} />
        </FullscreenOverlay>
      )}
      {anomalyFullscreen.expanded && (
        <FullscreenOverlay title="Anomaly from Mean" onClose={anomalyFullscreen.collapse}>
          <PlotlyChart data={anomalyData} layout={anomalyControls.layoutOverrides} height={640} downloadFilename={downloadFilename ? `${filters.parameter}-anomaly` : undefined} />
        </FullscreenOverlay>
      )}
    </div>
  );
}
