"use client";

import dynamic from "next/dynamic";
import type { Data, Layout } from "plotly.js";
import { useTheme } from "@/context/ThemeContext";
import { plotlyLayout, PLOTLY_CONFIG } from "./plotlyTheme";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

interface PlotlyChartProps {
  data: Data[];
  layout?: Partial<Layout>;
  style?: React.CSSProperties;
  height?: number;
  /** Filename for Plotly's built-in "download as PNG" modebar button. Pass
   * undefined to remove that button entirely (Master Plan §3 Phase 7 —
   * admin-controlled export toggles hide it per module when disabled). */
  downloadFilename?: string;
  resetKey?: number;
}

export default function PlotlyChart({ data, layout = {}, style, height = 320, downloadFilename, resetKey }: PlotlyChartProps) {
  const { theme } = useTheme();

  const config = downloadFilename
    ? { ...PLOTLY_CONFIG, toImageButtonOptions: { filename: downloadFilename, format: "png" as const } }
    : {
        ...PLOTLY_CONFIG,
        modeBarButtonsToRemove: [...PLOTLY_CONFIG.modeBarButtonsToRemove, "toImage"],
      };

  return (
    <Plot
      key={resetKey}
      data={data}
      layout={plotlyLayout(theme, layout) as Partial<Layout>}
      config={config}
      style={{ width: "100%", height, ...style }}
      useResizeHandler
    />
  );
}
