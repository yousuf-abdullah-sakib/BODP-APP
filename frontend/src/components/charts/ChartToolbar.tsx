"use client";

import { useState } from "react";
import type { Layout } from "plotly.js";
import { FullscreenButton } from "./FullscreenChartWrapper";
import ColorRampSelect from "@/components/ui/ColorRampSelect";
import type { ColorRampName } from "@/lib/geo/colorRamp";

export interface ChartControlOptions {
  legend?: boolean;
  grid?: boolean;
  resetView?: boolean;
  palette?: { value: ColorRampName; onChange: (ramp: ColorRampName) => void };
}

export function useChartControls(options: ChartControlOptions = {}) {
  const [showLegend, setShowLegend] = useState(true);
  const [showGrid, setShowGrid] = useState(true);
  const [resetKey, setResetKey] = useState(0);

  const layoutOverrides: Partial<Layout> = {};
  if (options.legend) layoutOverrides.showlegend = showLegend;
  if (options.grid) {
    layoutOverrides.xaxis = { showgrid: showGrid } as Layout["xaxis"];
    layoutOverrides.yaxis = { showgrid: showGrid } as Layout["yaxis"];
  }

  return {
    layoutOverrides,
    resetKey,
    showLegend,
    showGrid,
    toggleLegend: () => setShowLegend((v) => !v),
    toggleGrid: () => setShowGrid((v) => !v),
    resetView: () => setResetKey((k) => k + 1),
  };
}

interface ChartToolbarProps {
  onExpand: () => void;
  controls: ReturnType<typeof useChartControls>;
  options?: ChartControlOptions;
}

export default function ChartToolbar({ onExpand, controls, options = {} }: ChartToolbarProps) {
  return (
    <div className="chart-actions">
      {options.palette && (
        <ColorRampSelect compact value={options.palette.value} onChange={(r: ColorRampName) => options.palette!.onChange(r)} />
      )}
      {options.legend && (
        <button className={`chart-btn${controls.showLegend ? " active" : ""}`} title="Toggle legend" onClick={controls.toggleLegend}>
          🏷 Legend
        </button>
      )}
      {options.grid && (
        <button className={`chart-btn${controls.showGrid ? " active" : ""}`} title="Toggle grid lines" onClick={controls.toggleGrid}>
          ▦ Grid
        </button>
      )}
      {options.resetView && (
        <button className="chart-btn" title="Reset zoom/pan" onClick={controls.resetView}>
          ⟲ Reset
        </button>
      )}
      <FullscreenButton onClick={onExpand} />
    </div>
  );
}
