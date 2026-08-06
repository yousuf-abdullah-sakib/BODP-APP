"use client";

import { useRouter } from "next/navigation";
import CategoryPill from "./CategoryPill";
import type { DatasetSummary } from "@/lib/types/catalog";

export default function DatasetCard({ dataset }: { dataset: DatasetSummary }) {
  const router = useRouter();

  return (
    <div className="ds-card" onClick={() => router.push(`/catalog/${dataset.id}`)}>
      <div className="ds-card-head">
        <CategoryPill category={dataset.category} />
        <span className="ds-card-code">{dataset.code}</span>
      </div>
      <h3 className="ds-card-title">{dataset.title}</h3>
      <p className="ds-card-desc">{dataset.description}</p>
      <div className="ds-card-params">
        {dataset.parameters.slice(0, 3).map((p) => (
          <span key={p} className="chip">
            {p}
          </span>
        ))}
        {dataset.parameters.length > 3 && <span className="chip">+{dataset.parameters.length - 3}</span>}
      </div>
      <div className="ds-card-meta">
        <span>📍 {dataset.location ?? "Unknown location"}</span>
        <span>🗄️ {dataset.record_count.toLocaleString()} records</span>
      </div>
      <div className="ds-card-foot">
        <span className="ds-card-formats">{dataset.formats.join(" · ")}</span>
        <button
          className="ds-card-btn"
          onClick={(e) => {
            e.stopPropagation();
            router.push(`/catalog/${dataset.id}`);
          }}
        >
          View Dataset →
        </button>
      </div>
    </div>
  );
}
