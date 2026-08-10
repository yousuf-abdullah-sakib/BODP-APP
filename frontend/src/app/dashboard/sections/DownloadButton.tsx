"use client";

import { useState } from "react";
import type { ExtractionFormat, GrantSummary } from "@/lib/types/requests";

const FORMATS: { value: ExtractionFormat; label: string }[] = [
  { value: "csv", label: "CSV" },
  { value: "parquet", label: "Parquet" },
  { value: "netcdf", label: "NetCDF" },
  { value: "mat", label: "MATLAB (.mat)" },
];

interface DownloadButtonProps {
  grant: GrantSummary;
  busy: boolean;
  onDownload: (grant: GrantSummary, format: ExtractionFormat) => void;
  fullWidth?: boolean;
  buttonClassName?: string;
}

export default function DownloadButton({
  grant,
  busy,
  onDownload,
  fullWidth,
  buttonClassName = "btn-primary",
}: DownloadButtonProps) {
  const [format, setFormat] = useState<ExtractionFormat>("csv");

  return (
    <div
      style={{
        display: "flex",
        gap: "0.4rem",
        flex: fullWidth ? 1 : undefined,
        // Caps the whole row so it stays a fixed, sensible size regardless
        // of how wide the surrounding .dl-card grid cell happens to be —
        // .dl-grid's auto-fill/minmax columns don't grow monotonically
        // with viewport width (a card can be ~352px wide right before a
        // new column fits, then snap to ~260px right after), so anything
        // sized purely via flex:1 grows/shrinks with the card itself and
        // visibly balloons at the wider end. minWidth:0 still lets it
        // shrink below this cap inside a genuinely narrow card.
        maxWidth: fullWidth ? 220 : undefined,
        minWidth: 0,
      }}
    >
      <select
        className="filter-select"
        style={{ flex: fullWidth ? "0 1 100px" : undefined, maxWidth: 100, minWidth: 0 }}
        value={format}
        onChange={(e) => setFormat(e.target.value as ExtractionFormat)}
        disabled={busy}
      >
        {FORMATS.map((f) => (
          <option key={f.value} value={f.value}>
            {f.label}
          </option>
        ))}
      </select>
      <button
        className={buttonClassName}
        style={{ flex: "1 1 auto", maxWidth: 120, minWidth: 0 }}
        disabled={busy}
        onClick={() => onDownload(grant, format)}
      >
        {busy ? "Preparing…" : "⬇ Download"}
      </button>
    </div>
  );
}
