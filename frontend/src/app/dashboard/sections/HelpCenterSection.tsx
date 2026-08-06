"use client";

import { useEffect, useState } from "react";
import { getCmsBlocks } from "@/lib/api/me";
import type { CmsBlockSummary } from "@/lib/types/me";

export default function HelpCenterSection() {
  const [search, setSearch] = useState("");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<CmsBlockSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCmsBlocks("dashboard-help")
      .then((data) => {
        if (!cancelled) setBlocks(data);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load help articles.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = blocks.filter(
    (b) =>
      (b.label ?? "").toLowerCase().includes(search.toLowerCase()) ||
      (b.value ?? "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Help Center</div>
          <div className="dash-sub">Answers to common questions about using BODP.</div>
        </div>
      </div>

      <div className="filter-toolbar">
        <input
          type="text"
          placeholder="Search help articles…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading help articles…</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">🔍</div>
          <p>No help articles match your search.</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
          {filtered.map((b) => (
            <div
              key={b.key}
              className="panel"
              style={{ cursor: "pointer" }}
              onClick={() => setOpenKey(openKey === b.key ? null : b.key)}
            >
              <div className="panel-body">
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    fontWeight: 700,
                    fontSize: "0.86rem",
                    color: "var(--text-primary)",
                  }}
                >
                  {b.label}
                  <span style={{ color: "var(--accent)" }}>{openKey === b.key ? "−" : "+"}</span>
                </div>
                {openKey === b.key && (
                  <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginTop: "0.7rem", lineHeight: 1.7 }}>
                    {b.value}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
