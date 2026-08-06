"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import { parseShapefile } from "@/lib/geo/shapefileUpload";
import { createBoundary, deleteBoundary, getDefaultBoundary } from "@/lib/api/boundary";
import { ApiError } from "@/lib/api/client";
import type { BoundaryShapefileSummary } from "@/lib/types/visualize";

function countVertices(geo: GeoJSON.FeatureCollection): number {
  let count = 0;
  for (const f of geo.features) {
    if (!f.geometry) continue;
    if (f.geometry.type === "Polygon") count += f.geometry.coordinates.reduce((a, ring) => a + ring.length, 0);
    if (f.geometry.type === "MultiPolygon") count += f.geometry.coordinates.flat().reduce((a, ring) => a + ring.length, 0);
  }
  return count;
}

export default function DataVisualizationSection() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [defaultBoundary, setDefaultBoundaryState] = useState<BoundaryShapefileSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getDefaultBoundary()
      .then((b) => {
        if (!cancelled) setDefaultBoundaryState(b);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setUploading(true);
    try {
      const geo = await parseShapefile(f);
      const created = await createBoundary(f.name, geo, true);
      setDefaultBoundaryState(created);
      toast(`Default boundary updated from ${f.name}. The Visualization module will use this shapefile.`, "success");
    } catch (err) {
      if (err instanceof ApiError) {
        toast(err.message, "error");
      } else {
        toast("Could not read that file as a shapefile. Upload a .zip (shp+dbf+prj) bundle.", "error");
      }
    } finally {
      setUploading(false);
    }
  }

  async function handleReset() {
    if (!defaultBoundary) return;
    const ok = await confirm({
      title: "Reset to Default Boundary",
      message: "Remove the admin-uploaded boundary? The Visualization module will fall back to the built-in Bangladesh boundary.",
      confirmLabel: "Reset",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteBoundary(defaultBoundary.id);
      setDefaultBoundaryState(null);
      toast("Reverted to the built-in default boundary.", "info");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to reset boundary.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Data Visualization</div>
          <div className="dash-sub">Manage the default boundary shapefile used throughout the Visualization module.</div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1.4rem" }}>
        <div className="panel-head">
          <span className="panel-title">Default Boundary Shapefile</span>
        </div>
        <div className="panel-body">
          <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "1rem", lineHeight: 1.6 }}>
            This boundary is shown as the reference layer on the public Visualization page&apos;s Spatial Mapping
            module and used to clip interpolation results when no researcher-uploaded shapefile is active. Upload a
            new one to replace the built-in Bangladesh outline sitewide.
          </p>

          {loading ? (
            <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>Loading current boundary…</div>
          ) : (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "1rem",
                padding: "0.9rem 1.1rem",
                borderRadius: 10,
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                marginBottom: "1rem",
                flexWrap: "wrap",
              }}
            >
              <div>
                <div style={{ fontWeight: 700, fontSize: "0.86rem", color: "var(--text-primary)" }}>
                  {defaultBoundary ? defaultBoundary.name : "Built-in Bangladesh Boundary (default)"}
                </div>
                <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
                  {defaultBoundary
                    ? `${defaultBoundary.geojson.features.length} feature(s) · ~${countVertices(defaultBoundary.geojson)} vertices · admin-managed`
                    : "Currently serving the static /geo/bangladesh-boundary.geojson fallback."}
                </div>
              </div>
              <span className={`badge ${defaultBoundary ? "badge-approved" : "badge-pending"}`}>
                {defaultBoundary ? "● Custom Active" : "Default"}
              </span>
            </div>
          )}

          <div className="flex-gap">
            <button className="btn-primary" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
              {uploading ? "Uploading…" : "📁 Upload Shapefile (.zip)"}
            </button>
            {defaultBoundary && (
              <button className="btn-outline" onClick={handleReset}>
                ↺ Reset to Default
              </button>
            )}
          </div>
          <input ref={fileInputRef} type="file" accept=".zip,.shp" style={{ display: "none" }} onChange={handleUpload} />
        </div>
      </div>
    </>
  );
}
