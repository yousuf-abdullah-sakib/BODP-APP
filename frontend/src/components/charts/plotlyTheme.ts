import type { Theme } from "@/context/ThemeContext";

export function accentColor(theme: Theme) {
  return theme === "dark" ? "#2dd4bf" : "#0f766e";
}

export function plotlyLayout(theme: Theme, overrides: Record<string, unknown> = {}) {
  const isDark = theme === "dark";
  const gridColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(16,25,58,0.1)";
  const textColor = isDark ? "#b7bfe0" : "#4b5875";
  const paperBg = "rgba(0,0,0,0)";

  return {
    paper_bgcolor: paperBg,
    plot_bgcolor: paperBg,
    font: { family: "Segoe UI, system-ui, sans-serif", color: textColor, size: 11 },
    margin: { t: 30, r: 20, b: 40, l: 50 },
    xaxis: { gridcolor: gridColor, zerolinecolor: gridColor, color: textColor },
    yaxis: { gridcolor: gridColor, zerolinecolor: gridColor, color: textColor },
    legend: { font: { color: textColor, size: 10 }, orientation: "h", y: -0.2 },
    colorway: [accentColor(theme), "#16a34a", "#7c3aed", "#ea580c", "#0891b2", "#dc2626"],
    ...overrides,
  };
}

export const PLOTLY_CONFIG = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ["sendDataToCloud", "lasso2d", "select2d"] as string[],
};
