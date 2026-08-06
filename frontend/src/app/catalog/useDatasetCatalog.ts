import { useEffect, useState } from "react";
import { getCatalogTaxonomy, searchCatalog } from "@/lib/api/catalog";
import type { DatasetSort, DatasetSummary, TaxonomyOptions } from "@/lib/types/catalog";

export type { DatasetSort } from "@/lib/types/catalog";

export interface DatasetCatalogFilters {
  search: string;
  category: string;
  parameter: string;
  source: string;
  platform: string;
  format: string;
}

export const DEFAULT_DATASET_FILTERS: DatasetCatalogFilters = {
  search: "",
  category: "",
  parameter: "",
  source: "",
  platform: "",
  format: "",
};

const EMPTY_TAXONOMY: TaxonomyOptions = {
  categories: [],
  parameters: [],
  sources: [],
  platforms: [],
  formats: [],
};

/**
 * Backend-driven replacement for the prototype's client-side useMemo filter
 * (Master Plan §3 Phase 3 task 5) — search/filter/sort/relevance now happen
 * in Postgres via GET /catalog/search, and the filter dropdown options come
 * from the real, Redis-cached taxonomy endpoint instead of scanning a mock
 * array in memory.
 */
export function useDatasetCatalog(filters: DatasetCatalogFilters, sort: DatasetSort) {
  const [results, setResults] = useState<DatasetSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [taxonomy, setTaxonomy] = useState<TaxonomyOptions>(EMPTY_TAXONOMY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCatalogTaxonomy()
      .then(setTaxonomy)
      .catch(() => setTaxonomy(EMPTY_TAXONOMY));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Debounce free-text search so every keystroke doesn't fire a request;
    // dropdown filter changes (category/parameter/etc.) fire immediately.
    const delay = filters.search ? 300 : 0;
    const timer = setTimeout(() => {
      searchCatalog({
        search: filters.search || undefined,
        category: filters.category || undefined,
        parameter: filters.parameter || undefined,
        source: filters.source || undefined,
        platform: filters.platform || undefined,
        format: filters.format || undefined,
        sort,
      })
        .then((res) => {
          if (cancelled) return;
          setResults(res.results);
          setTotal(res.total);
        })
        .catch(() => {
          if (!cancelled) setError("Failed to load datasets. Please try again.");
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, delay);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [filters, sort]);

  return {
    filtered: results,
    total,
    categories: taxonomy.categories,
    parameters: taxonomy.parameters,
    sources: taxonomy.sources,
    platforms: taxonomy.platforms,
    formats: taxonomy.formats,
    loading,
    error,
  };
}
