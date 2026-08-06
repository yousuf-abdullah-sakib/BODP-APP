"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import CategoryPill from "@/components/ui/CategoryPill";
import { getMyGrants } from "@/lib/api/requests";
import DatasetDetailModal from "./DatasetDetailModal";
import DownloadButton from "./DownloadButton";
import { useExtractionDownload } from "./useExtractionDownload";
import type { GrantSummary } from "@/lib/types/requests";

export default function MyDatasetsSection() {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [viewing, setViewing] = useState<GrantSummary | null>(null);
  const [grants, setGrants] = useState<GrantSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { downloadingGrantId, download } = useExtractionDownload();

  useEffect(() => {
    let cancelled = false;
    getMyGrants()
      .then((data) => {
        if (!cancelled) setGrants(data);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load your datasets.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = grants.filter((g) => g.dataset.title.toLowerCase().includes(search.toLowerCase()));

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">My Datasets</div>
          <div className="dash-sub">Datasets you&apos;ve been granted access to.</div>
        </div>
        <button className="btn-outline" onClick={() => router.push("/catalog")}>
          + Request More
        </button>
      </div>

      <div className="filter-toolbar">
        <input
          type="text"
          placeholder="Search my datasets…"
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
          <p>Loading your datasets…</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">🗄️</div>
          <p>
            No datasets match your search.
            <br />
            <button className="panel-link" onClick={() => router.push("/catalog")}>
              browse more datasets
            </button>
          </p>
        </div>
      ) : (
        <div className="dl-grid" style={{ marginBottom: "1.6rem" }}>
          {filtered.map((g) => (
            <div className="dl-card" key={g.id}>
              <CategoryPill category={g.dataset.category} />
              <div className="dl-card-title">{g.dataset.title}</div>
              <div className="dl-card-meta">
                <span>📦 {g.dataset.code}</span>
              </div>
              <div className="dl-card-meta">
                <span>⏳ Expires {new Date(g.expires_at).toLocaleDateString()}</span>
              </div>
              <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.6rem", alignItems: "center" }}>
                <button className="btn-icon-sm" title="Dataset details" onClick={() => setViewing(g)}>
                  ℹ
                </button>
                <DownloadButton
                  grant={g}
                  busy={downloadingGrantId === g.id}
                  onDownload={download}
                  fullWidth
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {viewing && <DatasetDetailModal grant={viewing} onClose={() => setViewing(null)} />}
    </>
  );
}
