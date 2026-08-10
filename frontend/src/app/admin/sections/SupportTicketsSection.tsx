"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import {
  getSupportTicketAdmin,
  getSupportTicketsAdmin,
  replyToSupportTicket,
} from "@/lib/api/admin-support";
import type { SupportTicketAdminDetail, SupportTicketAdminSummary } from "@/lib/types/admin-support";
import SupportTicketReplyModal from "./SupportTicketReplyModal";

export default function SupportTicketsSection() {
  const { toast } = useToast();
  const [tickets, setTickets] = useState<SupportTicketAdminSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"" | "open" | "answered" | "closed">("");
  const [search, setSearch] = useState("");
  const [viewing, setViewing] = useState<SupportTicketAdminDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState<string | null>(null);

  function refetch() {
    setLoading(true);
    getSupportTicketsAdmin({ status_filter: filter || undefined, search: search || undefined })
      .then(setTickets)
      .catch(() => toast("Failed to load support tickets.", "error"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, search]);

  async function handleOpen(id: string) {
    setLoadingDetail(id);
    try {
      const detail = await getSupportTicketAdmin(id);
      setViewing(detail);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to load ticket.", "error");
    } finally {
      setLoadingDetail(null);
    }
  }

  async function handleReply(replyMessage: string) {
    if (!viewing) return;
    try {
      await replyToSupportTicket(viewing.id, { reply_message: replyMessage });
      toast("Reply sent.", "success");
      setViewing(null);
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to send reply.", "error");
      throw err;
    }
  }

  const openCount = tickets.filter((t) => t.status === "open").length;

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Support Tickets</div>
          <div className="dash-sub">Tickets submitted through the researcher dashboard.</div>
        </div>
      </div>

      <div className="filter-tab-row">
        <button className={`filter-tab-btn${filter === "" ? " active" : ""}`} onClick={() => setFilter("")}>
          All ({tickets.length})
        </button>
        <button
          className={`filter-tab-btn${filter === "open" ? " active" : ""}`}
          onClick={() => setFilter("open")}
        >
          Open ({openCount})
        </button>
        <button
          className={`filter-tab-btn${filter === "answered" ? " active" : ""}`}
          onClick={() => setFilter("answered")}
        >
          Answered
        </button>
        <button
          className={`filter-tab-btn${filter === "closed" ? " active" : ""}`}
          onClick={() => setFilter("closed")}
        >
          Closed
        </button>
      </div>

      <div className="filter-toolbar">
        <input
          type="text"
          placeholder="Search subject…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="panel">
          <div className="panel-body">Loading tickets…</div>
        </div>
      ) : tickets.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">🎫</div>
          <p>No support tickets yet.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>From</th>
                <th>Subject</th>
                <th>Priority</th>
                <th>Submitted</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <tr key={t.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{t.requester_name}</div>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                      {t.requester_email}
                    </div>
                  </td>
                  <td style={{ fontSize: "0.82rem" }}>{t.subject}</td>
                  <td style={{ fontSize: "0.78rem", textTransform: "capitalize" }}>{t.priority}</td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                    {new Date(t.created_at).toLocaleString()}
                  </td>
                  <td>
                    <span
                      className={`badge badge-${
                        t.status === "answered" ? "approved" : t.status === "closed" ? "suspended" : "pending"
                      }`}
                    >
                      {t.status}
                    </span>
                  </td>
                  <td>
                    <button
                      className="btn-icon-sm"
                      title="Open ticket"
                      onClick={() => handleOpen(t.id)}
                      disabled={loadingDetail === t.id}
                    >
                      {loadingDetail === t.id ? "…" : "👁"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {viewing && (
        <SupportTicketReplyModal
          ticket={viewing}
          onClose={() => setViewing(null)}
          onReply={handleReply}
        />
      )}
    </>
  );
}
