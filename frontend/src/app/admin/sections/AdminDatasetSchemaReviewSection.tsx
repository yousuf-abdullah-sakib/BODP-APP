"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import {
  getDatasetSchema,
  getDatasetsForReview,
  markDatasetReviewed,
  updateVariableRoles,
} from "@/lib/api/admin-dataset-schema";
import type {
  DatasetSchemaDetail,
  DatasetSchemaReviewSummary,
  VariableRole,
} from "@/lib/types/admin-dataset-schema";
import { VARIABLE_ROLE_LABELS } from "@/lib/types/admin-dataset-schema";

const ALL_ROLES = Object.keys(VARIABLE_ROLE_LABELS) as VariableRole[];

const TABS: { key: "pending" | "all"; label: string }[] = [
  { key: "pending", label: "Pending Review" },
  { key: "all", label: "All" },
];

export default function AdminDatasetSchemaReviewSection() {
  const { toast } = useToast();
  const [tab, setTab] = useState<"pending" | "all">("pending");
  const [rows, setRows] = useState<DatasetSchemaReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DatasetSchemaDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [savingVariableId, setSavingVariableId] = useState<string | null>(null);
  const [markingReviewed, setMarkingReviewed] = useState(false);

  function refetch() {
    setLoading(true);
    getDatasetsForReview(tab === "pending")
      .then(setRows)
      .catch(() => setError("Failed to load datasets."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  async function refetchDetail(datasetId: string) {
    setDetailLoading(true);
    try {
      setDetail(await getDatasetSchema(datasetId));
    } catch {
      toast("Failed to load dataset schema.", "error");
    } finally {
      setDetailLoading(false);
    }
  }

  function toggleExpand(datasetId: string) {
    if (expandedId === datasetId) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(datasetId);
    setDetail(null);
    refetchDetail(datasetId);
  }

  async function handleToggleRole(variableId: string, role: VariableRole, currentRoles: string[]) {
    if (!detail) return;
    const nextRoles = currentRoles.includes(role)
      ? currentRoles.filter((r) => r !== role)
      : [...currentRoles, role];

    setSavingVariableId(variableId);
    try {
      const updated = await updateVariableRoles(detail.dataset_id, variableId, nextRoles);
      setDetail({
        ...detail,
        schema_reviewed_at: null,
        schema_reviewed_by_name: null,
        variables: detail.variables.map((v) => (v.id === variableId ? updated : v)),
      });
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to update role.", "error");
    } finally {
      setSavingVariableId(null);
    }
  }

  async function handleMarkReviewed() {
    if (!detail) return;
    setMarkingReviewed(true);
    try {
      await markDatasetReviewed(detail.dataset_id);
      toast(`Schema approved for "${detail.dataset_title}".`, "success");
      await refetchDetail(detail.dataset_id);
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to mark as reviewed.", "error");
    } finally {
      setMarkingReviewed(false);
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Dataset Schema Review</div>
          <div className="dash-sub">
            Review auto-detected variables and assign roles before they&apos;re used for filtering or visualization.
          </div>
        </div>
      </div>

      <div className="filter-tab-row">
        {TABS.map((t) => (
          <button key={t.key} className={`filter-tab-btn${tab === t.key ? " active" : ""}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
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
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {rows.length === 0 && (
            <div className="empty-state">
              <div className="es-icon">✅</div>
              <p>{tab === "pending" ? "No datasets pending schema review." : "No ingested datasets yet."}</p>
            </div>
          )}
          {rows.map((row) => (
            <div className="panel" key={row.dataset_id}>
              <div className="panel-head" style={{ cursor: "pointer" }} onClick={() => toggleExpand(row.dataset_id)}>
                <div>
                  <span className="panel-title">{row.dataset_title}</span>{" "}
                  <span className="chip">{row.dataset_code}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
                  {row.schema_reviewed_at ? (
                    <span className="badge badge-approved">✓ Reviewed</span>
                  ) : (
                    <span className="badge badge-pending">
                      {row.unassigned_count}/{row.variable_count} unassigned
                    </span>
                  )}
                </div>
              </div>

              {expandedId === row.dataset_id && (
                <div className="panel-body">
                  {detailLoading || !detail ? (
                    <p style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>Loading variables…</p>
                  ) : (
                    <>
                      {detail.schema_reviewed_at && (
                        <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginBottom: "0.9rem" }}>
                          Reviewed by {detail.schema_reviewed_by_name ?? "unknown"} on{" "}
                          {new Date(detail.schema_reviewed_at).toLocaleString()}
                        </div>
                      )}
                      <div style={{ display: "flex", flexDirection: "column", gap: "0.8rem" }}>
                        {detail.variables.map((v) => (
                          <div key={v.id} className="req-dataset-row" style={{ flexDirection: "column", alignItems: "stretch", gap: "0.5rem" }}>
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                              <div>
                                <b>{v.name}</b>{" "}
                                <span className="chip">{v.data_type}</span>
                                {v.is_dimension && <span className="chip" style={{ marginLeft: "0.3rem" }}>dimension-detected</span>}
                              </div>
                              <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                                {v.min_value !== null && v.max_value !== null
                                  ? `${v.min_value} – ${v.max_value}${v.unit ? ` ${v.unit}` : ""}`
                                  : v.distinct_values
                                    ? `${v.distinct_values.length} distinct value(s)`
                                    : null}
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                              {ALL_ROLES.map((role) => (
                                <label
                                  key={role}
                                  className={`chip-checkbox${v.roles.includes(role) ? " active" : ""}`}
                                  style={{ opacity: savingVariableId === v.id ? 0.6 : 1 }}
                                >
                                  <input
                                    type="checkbox"
                                    checked={v.roles.includes(role)}
                                    disabled={savingVariableId === v.id}
                                    onChange={() => handleToggleRole(v.id, role, v.roles)}
                                  />
                                  {VARIABLE_ROLE_LABELS[role]}
                                </label>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>

                      <div className="req-actions-row" style={{ marginTop: "1.2rem" }}>
                        <button
                          className="btn-success-sm"
                          disabled={markingReviewed || detail.variables.some((v) => v.roles.length === 0)}
                          onClick={handleMarkReviewed}
                        >
                          {markingReviewed ? "Approving…" : "✓ Approve Schema"}
                        </button>
                      </div>
                      {detail.variables.some((v) => v.roles.length === 0) && !detail.schema_reviewed_at && (
                        <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
                          Every variable needs at least one role before this dataset can be approved.
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
