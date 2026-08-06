"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { createSupportTicket, getSupportTickets } from "@/lib/api/me";
import { ApiError } from "@/lib/api/client";
import type { SupportTicketSummary } from "@/lib/types/me";

const CATEGORIES = ["Technical Support", "Data Request Help", "Account Issue", "Other"];

export default function ContactSupportSection() {
  const { toast } = useToast();
  const [tickets, setTickets] = useState<SupportTicketSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const [subject, setSubject] = useState("");
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [priority, setPriority] = useState<"low" | "medium" | "high">("medium");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    getSupportTickets()
      .then((data) => {
        if (!cancelled) setTickets(data);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function submit() {
    if (!subject.trim() || !message.trim()) {
      toast("Please fill in the subject and message.", "error");
      return;
    }
    setSubmitting(true);
    try {
      const ticket = await createSupportTicket({
        subject: subject.trim(),
        category,
        priority,
        message: message.trim(),
      });
      setTickets((prev) => [ticket, ...prev]);
      toast("Support ticket submitted. We'll respond within 2 business days.", "success");
      setSubject("");
      setMessage("");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to submit ticket.", "error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Contact Support</div>
          <div className="dash-sub">Open a support ticket with the BODP team.</div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1.4rem" }}>
        <div className="panel-head">
          <span className="panel-title">New Ticket</span>
        </div>
        <div className="panel-body">
          <div className="form-group">
            <label className="form-label">Subject *</label>
            <input className="form-input" value={subject} onChange={(e) => setSubject(e.target.value)} />
          </div>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Category</label>
              <select className="form-select" value={category} onChange={(e) => setCategory(e.target.value)}>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Priority</label>
              <select
                className="form-select"
                value={priority}
                onChange={(e) => setPriority(e.target.value as "low" | "medium" | "high")}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Message *</label>
            <textarea className="form-textarea" value={message} onChange={(e) => setMessage(e.target.value)} />
          </div>
          <button className="btn-primary" onClick={submit} disabled={submitting}>
            {submitting ? "Submitting…" : "Submit Ticket →"}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">My Support Tickets</span>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          {loading ? (
            <div style={{ padding: "1.2rem", fontSize: "0.82rem", color: "var(--text-muted)" }}>
              Loading tickets…
            </div>
          ) : tickets.length === 0 ? (
            <div style={{ padding: "1.2rem", fontSize: "0.82rem", color: "var(--text-muted)" }}>
              No support tickets yet.
            </div>
          ) : (
            <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>Subject</th>
                    <th>Category</th>
                    <th>Priority</th>
                    <th>Status</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {tickets.map((t) => (
                    <tr key={t.id}>
                      <td style={{ fontWeight: 500, maxWidth: 260 }}>{t.subject}</td>
                      <td style={{ fontSize: "0.8rem" }}>{t.category ?? "—"}</td>
                      <td>
                        <span
                          className={`badge ${
                            t.priority === "high"
                              ? "badge-alert"
                              : t.priority === "medium"
                                ? "badge-caution"
                                : "badge-normal"
                          }`}
                        >
                          {t.priority}
                        </span>
                      </td>
                      <td>
                        <span
                          className={`badge ${
                            t.status === "closed"
                              ? "badge-revoked"
                              : t.status === "answered"
                                ? "badge-approved"
                                : "badge-pending"
                          }`}
                        >
                          {t.status}
                        </span>
                      </td>
                      <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                        {new Date(t.created_at).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
