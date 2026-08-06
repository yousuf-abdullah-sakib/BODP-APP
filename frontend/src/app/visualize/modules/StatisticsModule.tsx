"use client";

import { useEffect, useState } from "react";
import PlotlyChart from "@/components/charts/PlotlyChart";
import ChartToolbar, { useChartControls } from "@/components/charts/ChartToolbar";
import { useFullscreenChart, FullscreenOverlay } from "@/components/charts/FullscreenChartWrapper";
import type { ColorRampName } from "@/lib/geo/colorRamp";
import { ApiError } from "@/lib/api/client";
import { postStatistics } from "@/lib/api/visualize";
import { toVizFilterParams, type VizFilters } from "../useVizFilters";
import { useVizExportSettings } from "../useVizExportSettings";
import type { StatisticsResponse } from "@/lib/types/visualize";
import type { Data } from "plotly.js";

const HISTOGRAM_COLOR = "#0891b2";
const ANOMALY_POS_COLOR = "#dc2626";
const ANOMALY_NEG_COLOR = "#0f766e";

interface StatisticsModuleProps {
  filters: VizFilters;
}

export default function StatisticsModule({ filters }: StatisticsModuleProps) {
  const [data, setData] = useState<StatisticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const exportsEnabled = useVizExportSettings();

  const boxFullscreen = useFullscreenChart();
  const histFullscreen = useFullscreenChart();
  const anomalyFullscreen = useFullscreenChart();
  const decompFullscreen = useFullscreenChart();
  const calendarFullscreen = useFullscreenChart();

  const boxControls = useChartControls({ legend: true, resetView: true });
  const histControls = useChartControls({ grid: true, resetView: true });
  const anomalyControls = useChartControls({ grid: true, resetView: true });
  const decompControls = useChartControls({ legend: true, resetView: true });
  const calendarControls = useChartControls({ resetView: true });
  const [calendarRamp, setCalendarRamp] = useState<ColorRampName>("YlOrRd");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    postStatistics({ parameter: filters.parameter, ...toVizFilterParams(filters) })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load statistics.");
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
        <p>Loading statistics…</p>
      </div>
    );
  }

  const { box_plot, histogram, annual_anomalies, decomposition, calendar_heatmap } = data;
  const parameter = filters.parameter;
  const dl = (suffix: string) => (exportsEnabled.statistics ? `${parameter}-${suffix}` : undefined);

  const boxData: Data[] = box_plot.map((s) => ({ y: s.values, type: "box", name: s.station } as Data));
  const histData: Data[] = [{ x: histogram, type: "histogram", marker: { color: HISTOGRAM_COLOR } } as Data];
  const anomalyData: Data[] = [
    {
      x: annual_anomalies.map((a) => String(a.year)),
      y: annual_anomalies.map((a) => a.anomaly),
      type: "bar",
      marker: { color: annual_anomalies.map((a) => (a.anomaly >= 0 ? ANOMALY_POS_COLOR : ANOMALY_NEG_COLOR)) },
    } as Data,
  ];
  const decompData: Data[] = [
    { x: decomposition.dates, y: decomposition.trend.map((t, i) => t + decomposition.seasonal[i] + decomposition.residual[i]), type: "scatter", mode: "lines", name: "Original" } as Data,
    { x: decomposition.dates, y: decomposition.trend, type: "scatter", mode: "lines", name: "Trend", xaxis: "x", yaxis: "y2" } as Data,
    { x: decomposition.dates, y: decomposition.seasonal, type: "scatter", mode: "lines", name: "Seasonal", yaxis: "y3" } as Data,
    { x: decomposition.dates, y: decomposition.residual, type: "scatter", mode: "lines", name: "Residual", yaxis: "y4" } as Data,
  ];
  const calendarData: Data[] = [
    {
      z: calendar_heatmap.z,
      x: calendar_heatmap.months,
      y: calendar_heatmap.years.map(String),
      type: "heatmap",
      colorscale: calendarRamp,
      colorbar: { title: { text: parameter } },
    } as Data,
  ];

  return (
    <div>
      <div className="chart-row-3">
        <div className="chart-container">
          <div className="chart-head">
            <div className="chart-title">Box Plot — All Stations</div>
            <ChartToolbar onExpand={boxFullscreen.expand} controls={boxControls} options={{ legend: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart data={boxData} layout={boxControls.layoutOverrides} resetKey={boxControls.resetKey} height={280} downloadFilename={dl("box-plot")} />
          </div>
        </div>
        <div className="chart-container">
          <div className="chart-head">
            <div className="chart-title">Histogram</div>
            <ChartToolbar onExpand={histFullscreen.expand} controls={histControls} options={{ grid: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart data={histData} layout={histControls.layoutOverrides} resetKey={histControls.resetKey} height={280} downloadFilename={dl("histogram")} />
          </div>
        </div>
        <div className="chart-container">
          <div className="chart-head">
            <div className="chart-title">Annual Anomalies</div>
            <ChartToolbar onExpand={anomalyFullscreen.expand} controls={anomalyControls} options={{ grid: true, resetView: true }} />
          </div>
          <div className="chart-body">
            <PlotlyChart data={anomalyData} layout={anomalyControls.layoutOverrides} resetKey={anomalyControls.resetKey} height={280} downloadFilename={dl("annual-anomalies")} />
          </div>
        </div>
      </div>
      {boxFullscreen.expanded && (
        <FullscreenOverlay title="Box Plot — All Stations" onClose={boxFullscreen.collapse}>
          <PlotlyChart data={boxData} layout={boxControls.layoutOverrides} height={640} downloadFilename={dl("box-plot")} />
        </FullscreenOverlay>
      )}
      {histFullscreen.expanded && (
        <FullscreenOverlay title="Histogram" onClose={histFullscreen.collapse}>
          <PlotlyChart data={histData} layout={histControls.layoutOverrides} height={640} downloadFilename={dl("histogram")} />
        </FullscreenOverlay>
      )}
      {anomalyFullscreen.expanded && (
        <FullscreenOverlay title="Annual Anomalies" onClose={anomalyFullscreen.collapse}>
          <PlotlyChart data={anomalyData} layout={anomalyControls.layoutOverrides} height={640} downloadFilename={dl("annual-anomalies")} />
        </FullscreenOverlay>
      )}

      <div className="chart-container" style={{ marginBottom: "1.2rem" }}>
        <div className="chart-head">
          <div className="chart-title">Time-Series Decomposition (Trend + Seasonal + Residual)</div>
          <ChartToolbar onExpand={decompFullscreen.expand} controls={decompControls} options={{ legend: true, resetView: true }} />
        </div>
        <div className="chart-body">
          <PlotlyChart
            data={decompData}
            layout={{ grid: { rows: 4, columns: 1, pattern: "independent" }, ...decompControls.layoutOverrides }}
            resetKey={decompControls.resetKey}
            height={420}
            downloadFilename={dl("decomposition")}
          />
        </div>
      </div>
      {decompFullscreen.expanded && (
        <FullscreenOverlay title="Time-Series Decomposition" onClose={decompFullscreen.collapse}>
          <PlotlyChart
            data={decompData}
            layout={{ grid: { rows: 4, columns: 1, pattern: "independent" }, ...decompControls.layoutOverrides }}
            height={720}
            downloadFilename={dl("decomposition")}
          />
        </FullscreenOverlay>
      )}

      <div className="chart-container">
        <div className="chart-head">
          <div className="chart-title">Calendar Heatmap — Value Intensity by Month</div>
          <ChartToolbar
            onExpand={calendarFullscreen.expand}
            controls={calendarControls}
            options={{ resetView: true, palette: { value: calendarRamp, onChange: setCalendarRamp } }}
          />
        </div>
        <div className="chart-body">
          <PlotlyChart data={calendarData} resetKey={calendarControls.resetKey} height={260} downloadFilename={dl("calendar-heatmap")} />
        </div>
      </div>
      {calendarFullscreen.expanded && (
        <FullscreenOverlay title="Calendar Heatmap" onClose={calendarFullscreen.collapse}>
          <PlotlyChart data={calendarData} height={640} downloadFilename={dl("calendar-heatmap")} />
        </FullscreenOverlay>
      )}
    </div>
  );
}
