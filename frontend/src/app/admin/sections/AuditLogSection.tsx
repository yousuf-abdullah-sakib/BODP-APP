"use client";

import { useEffect, useState } from "react";
import Pagination from "@/components/ui/Pagination";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { exportAuditLogCsv, getAuditLog } from "@/lib/api/admin-audit";
import { TYPE_OPTIONS, TYPE_STYLE } from "@/lib/types/admin-audit";
import type { AuditActionType, AuditLogEntryPublic } from "@/lib/types/admin-audit";

const PAGE_SIZE = 8;

export default function AuditLogSection() {
  const { toast } = useToast();
  const [items, setItems] = useState<AuditLogEntryPublic[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<"" | AuditActionType>("");
  const [page, setPage] = useState(1);

  function refetch() {
    setLoading(true);
    getAuditLog({ search: search || undefined, action_type: typeFilter || undefined, page, page_size: PAGE_SIZE })
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      .catch(() => toast("Failed to load audit log.", "error"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, typeFilter, page]);

  async function handleExport() {
    try {
      await exportAuditLogCsv({ search: search || undefined, action_type: typeFilter || undefined });
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to export audit log.", "error");
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Audit Log</div>
          <div className="dash-sub">Full history of administrative actions.</div>
        </div>
        <button className="btn-outline" onClick={handleExport}>
          ⬇ Export CSV
        </button>
      </div>

      <div className="filter-toolbar">
        <input
          type="text"
          placeholder="Search actor, email, action, or target…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <select
          value={typeFilter}
          onChange={(e) => {
            setTypeFilter(e.target.value as typeof typeFilter);
            setPage(1);
          }}
        >
          {TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading audit log…</p>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">📝</div>
          <p>No audit entries match your filters.</p>
        </div>
      ) : (
        <div className="table-wrap audit-table-wrap">
          <table className="audit-table">
            <thead>
              <tr>
                <th className="audit-col-type"></th>
                <th className="audit-col-time">Date &amp; Time</th>
                <th className="audit-col-actor">Actor Name</th>
                <th className="audit-col-email">Actor Email</th>
                <th className="audit-col-action">Action</th>
                <th className="audit-col-target">Target / Details</th>
                <th className="audit-col-ip">IP Address</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id} style={{ "--audit-color": TYPE_STYLE[a.action_type].color } as React.CSSProperties}>
                  <td className="audit-col-type">
                    <span className="audit-type-badge" title={a.action_type}>
                      {TYPE_STYLE[a.action_type].icon}
                    </span>
                  </td>
                  <td className="audit-col-time" title={new Date(a.created_at).toLocaleString()}>
                    {new Date(a.created_at).toLocaleString()}
                  </td>
                  <td className="audit-col-actor" title={a.actor_name ?? "—"}>
                    {a.actor_name ?? "—"}
                  </td>
                  <td className="audit-col-email" title={a.actor_email ?? "—"}>
                    {a.actor_email ?? "—"}
                  </td>
                  <td className="audit-col-action" title={a.action}>
                    {a.action}
                  </td>
                  <td className="audit-col-target" title={a.target ?? "—"}>
                    {a.target ?? "—"}
                  </td>
                  <td className="audit-col-ip" title={a.ip_address ?? "—"}>
                    {a.ip_address ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "center", marginTop: "0.5rem" }}>
        <Pagination page={page} totalPages={totalPages} onChange={setPage} />
      </div>
    </>
  );
}
