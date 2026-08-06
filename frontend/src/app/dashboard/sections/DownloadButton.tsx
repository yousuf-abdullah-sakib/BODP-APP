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
    <div style={{ display: "flex", gap: "0.4rem", flex: fullWidth ? 1 : undefined }}>
      <select
        className="filter-select"
        style={{ flex: fullWidth ? "0 0 auto" : undefined, maxWidth: 120 }}
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
        style={{ flex: 1 }}
        disabled={busy}
        onClick={() => onDownload(grant, format)}
      >
        {busy ? "Preparing…" : "⬇ Download"}
      </button>
    </div>
  );
}
