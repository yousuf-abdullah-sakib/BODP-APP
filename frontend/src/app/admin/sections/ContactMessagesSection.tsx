"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import {
  getContactSubmission,
  getContactSubmissions,
  replyToContactSubmission,
} from "@/lib/api/admin-contact";
import { CONTACT_SUBJECT_LABELS } from "@/lib/types/admin-contact";
import type {
  ContactSubmissionAdminDetail,
  ContactSubmissionAdminSummary,
} from "@/lib/types/admin-contact";
import ContactReplyModal from "./ContactReplyModal";

export default function ContactMessagesSection() {
  const { toast } = useToast();
  const [submissions, setSubmissions] = useState<ContactSubmissionAdminSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"" | "new" | "replied">("");
  const [search, setSearch] = useState("");
  const [viewing, setViewing] = useState<ContactSubmissionAdminDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState<string | null>(null);

  function refetch() {
    setLoading(true);
    getContactSubmissions({ status_filter: filter || undefined, search: search || undefined })
      .then(setSubmissions)
      .catch(() => toast("Failed to load contact messages.", "error"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, search]);

  async function handleOpen(id: string) {
    setLoadingDetail(id);
    try {
      const detail = await getContactSubmission(id);
      setViewing(detail);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to load message.", "error");
    } finally {
      setLoadingDetail(null);
    }
  }

  async function handleReply(replyMessage: string) {
    if (!viewing) return;
    try {
      await replyToContactSubmission(viewing.id, { reply_message: replyMessage });
      toast("Reply sent.", "success");
      setViewing(null);
      refetch();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to send reply.", "error");
      throw err;
    }
  }

  const newCount = submissions.filter((s) => s.status === "new").length;

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Contact Information</div>
          <div className="dash-sub">Messages submitted through the public Contact page.</div>
        </div>
      </div>

      <div className="filter-tab-row">
        <button className={`filter-tab-btn${filter === "" ? " active" : ""}`} onClick={() => setFilter("")}>
          All ({submissions.length})
        </button>
        <button
          className={`filter-tab-btn${filter === "new" ? " active" : ""}`}
          onClick={() => setFilter("new")}
        >
          New ({newCount})
        </button>
        <button
          className={`filter-tab-btn${filter === "replied" ? " active" : ""}`}
          onClick={() => setFilter("replied")}
        >
          Replied
        </button>
      </div>

      <div className="filter-toolbar">
        <input
          type="text"
          placeholder="Search name, email, or subject…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="panel">
          <div className="panel-body">Loading messages…</div>
        </div>
      ) : submissions.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">✉</div>
          <p>No contact messages yet.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>From</th>
                <th>Subject</th>
                <th>Received</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {submissions.map((s) => (
                <tr key={s.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{s.name}</div>
                    <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>{s.email}</div>
                  </td>
                  <td style={{ fontSize: "0.82rem" }}>
                    {CONTACT_SUBJECT_LABELS[s.subject] ?? s.subject}
                  </td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                    {new Date(s.created_at).toLocaleString()}
                  </td>
                  <td>
                    <span className={`badge badge-${s.status === "replied" ? "approved" : "pending"}`}>
                      {s.status}
                    </span>
                  </td>
                  <td>
                    <button
                      className="btn-icon-sm"
                      title="Open message"
                      onClick={() => handleOpen(s.id)}
                      disabled={loadingDetail === s.id}
                    >
                      {loadingDetail === s.id ? "…" : "👁"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {viewing && (
        <ContactReplyModal
          submission={viewing}
          onClose={() => setViewing(null)}
          onReply={handleReply}
        />
      )}
    </>
  );
}
