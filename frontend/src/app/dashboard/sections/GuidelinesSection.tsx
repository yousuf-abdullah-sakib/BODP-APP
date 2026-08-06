"use client";

import { useEffect, useState } from "react";
import { getCmsBlocks } from "@/lib/api/me";
import type { CmsBlockSummary } from "@/lib/types/me";

const ICONS: Record<string, string> = {
  "dashboard-guidelines-license": "📜",
  "dashboard-guidelines-citation": "✍️",
  "dashboard-guidelines-acceptable-use": "✅",
  "dashboard-guidelines-access-renewal": "🔒",
};

export default function GuidelinesSection() {
  const [blocks, setBlocks] = useState<CmsBlockSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCmsBlocks("dashboard-guidelines")
      .then((data) => {
        if (!cancelled) setBlocks(data);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load guidelines.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Data Usage Guidelines</div>
          <div className="dash-sub">License terms, attribution, and acceptable use.</div>
        </div>
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading guidelines…</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1.2rem" }}>
          {blocks.map((b) => (
            <div className="panel" key={b.key}>
              <div className="panel-head">
                <span className="panel-title">
                  {ICONS[b.key] ?? "📄"} {b.label}
                </span>
              </div>
              <div className="panel-body">
                <p
                  style={{
                    fontSize: "0.85rem",
                    color: "var(--text-secondary)",
                    lineHeight: 1.75,
                    whiteSpace: "pre-line",
                  }}
                >
                  {b.value}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
