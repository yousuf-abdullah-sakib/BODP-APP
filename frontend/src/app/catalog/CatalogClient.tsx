"use client";

import { useState } from "react";
import DatasetCard from "@/components/ui/DatasetCard";
import {
  useDatasetCatalog,
  DEFAULT_DATASET_FILTERS,
  type DatasetCatalogFilters,
  type DatasetSort,
} from "./useDatasetCatalog";

export default function CatalogClient() {
  const [filters, setFilters] = useState<DatasetCatalogFilters>(DEFAULT_DATASET_FILTERS);
  const [sort, setSort] = useState<DatasetSort>("relevance");
  const { filtered, total, categories, parameters, sources, platforms, formats, loading, error } =
    useDatasetCatalog(filters, sort);

  function update<K extends keyof DatasetCatalogFilters>(key: K, value: DatasetCatalogFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  function resetFilters() {
    setFilters(DEFAULT_DATASET_FILTERS);
  }

  return (
    <>
      <div className="page-hero">
        <div className="section-tag">🌊 Open Data</div>
        <h1>Browse Datasets</h1>
        <p>Search and explore the BODP dataset catalog. Open a dataset to filter, preview, and request access.</p>
        <div className="ph-meta">
          <div className="ph-meta-item">
            Datasets: <b>{total}</b>
          </div>
          <div className="ph-meta-item">
            Sources: <b>{sources.length}</b>
          </div>
          <div className="ph-meta-item">
            Categories: <b>{categories.length}</b>
          </div>
          <div className="ph-meta-item">
            License: <b>CC BY 4.0</b>
          </div>
        </div>
      </div>

      <div className="main-layout">
        <aside className="sidebar">
          <div className="sidebar-section">
            <h4>🔍 Text Search</h4>
            <div className="filter-group">
              <input
                className="filter-input"
                type="text"
                placeholder="Search title, location, parameter, source, platform…"
                value={filters.search}
                onChange={(e) => update("search", e.target.value)}
              />
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.4rem", lineHeight: 1.5 }}>
                Matches dataset titles, codes, locations, parameters, sources, platforms, categories, and descriptions.
              </div>
            </div>
          </div>

          <div className="sidebar-section">
            <h4>📁 Data Filters</h4>
            <div className="filter-group">
              <span className="filter-label">Category</span>
              <select className="filter-select" value={filters.category} onChange={(e) => update("category", e.target.value)}>
                <option value="">All Categories</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="filter-group">
              <span className="filter-label">Parameter</span>
              <select className="filter-select" value={filters.parameter} onChange={(e) => update("parameter", e.target.value)}>
                <option value="">All Parameters</option>
                {parameters.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div className="filter-group">
              <span className="filter-label">Source</span>
              <select className="filter-select" value={filters.source} onChange={(e) => update("source", e.target.value)}>
                <option value="">All Sources</option>
                {sources.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="filter-group">
              <span className="filter-label">Observation Platform</span>
              <select className="filter-select" value={filters.platform} onChange={(e) => update("platform", e.target.value)}>
                <option value="">All Platforms</option>
                {platforms.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div className="filter-group">
              <span className="filter-label">Data Format</span>
              <select className="filter-select" value={filters.format} onChange={(e) => update("format", e.target.value)}>
                <option value="">All Formats</option>
                {formats.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </div>
            <div className="filter-actions">
              <button className="btn-reset" style={{ gridColumn: "1 / -1" }} onClick={resetFilters}>
                Reset All
              </button>
            </div>
          </div>
        </aside>

        <main className="content-area">
          <div className="table-toolbar">
            <div className="table-count">
              Showing <b>{filtered.length}</b> of <b>{total}</b> datasets
            </div>
            <div className="toolbar-right">
              <div className="per-page-wrap">
                Sort by:
                <select value={sort} onChange={(e) => setSort(e.target.value as DatasetSort)}>
                  <option value="relevance">Relevance</option>
                  <option value="title">Title (A–Z)</option>
                  <option value="updated">Recently Updated</option>
                  <option value="records">Most Records</option>
                </select>
              </div>
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
              <p>Loading datasets…</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="empty-state">
              <div className="es-icon">🔍</div>
              <p>
                No datasets match your filters.
                <br />
                Try adjusting the search criteria.
              </p>
            </div>
          ) : (
            <div className="ds-card-grid">
              {filtered.map((d) => (
                <DatasetCard key={d.id} dataset={d} />
              ))}
            </div>
          )}
        </main>
      </div>

      <div className="data-footer">
        <span>© 2024 Bangladesh Oceanographic Data Portal — CC BY 4.0</span>
      </div>
    </>
  );
}
