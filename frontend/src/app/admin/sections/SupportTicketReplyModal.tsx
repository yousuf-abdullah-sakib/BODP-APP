"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import type { SupportTicketAdminDetail } from "@/lib/types/admin-support";

interface SupportTicketReplyModalProps {
  ticket: SupportTicketAdminDetail;
  onClose: () => void;
  onReply: (replyMessage: string) => Promise<void>;
}

export default function SupportTicketReplyModal({
  ticket,
  onClose,
  onReply,
}: SupportTicketReplyModalProps) {
  const { toast } = useToast();
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  async function submit() {
    if (!reply.trim()) {
      toast("Please write a reply before sending.", "error");
      return;
    }
    setSending(true);
    try {
      await onReply(reply.trim());
    } finally {
      setSending(false);
    }
  }

  return (
    <Modal
      title="Support Ticket"
      onClose={onClose}
      large
      footer={
        ticket.status === "answered" ? (
          <button className="btn-ghost-sm" onClick={onClose}>
            Close
          </button>
        ) : (
          <>
            <button className="btn-ghost-sm" onClick={onClose}>
              Cancel
            </button>
            <button className="btn-primary-sm" onClick={submit} disabled={sending}>
              {sending ? "Sending…" : "↩ Send Reply"}
            </button>
          </>
        )
      }
    >
      <div className="form-group">
        <label className="form-label">From</label>
        <div>
          <b>{ticket.requester_name}</b> — {ticket.requester_email}
        </div>
      </div>
      <div className="form-row">
        <div className="form-group">
          <label className="form-label">Subject</label>
          <div>{ticket.subject}</div>
        </div>
        <div className="form-group">
          <label className="form-label">Category / Priority</label>
          <div>
            {ticket.category ?? "—"} · {ticket.priority}
          </div>
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">Message</label>
        <div
          style={{
            background: "var(--bg-primary)",
            border: "1px solid var(--border)",
            borderRadius: "8px",
            padding: "0.8rem",
            fontSize: "0.85rem",
            whiteSpace: "pre-wrap",
          }}
        >
          {ticket.message}
        </div>
      </div>

      {ticket.reply_message && (
        <div className="form-group">
          <label className="form-label">
            Reply sent {ticket.replied_by_name ? `by ${ticket.replied_by_name}` : ""}
            {ticket.replied_at ? ` — ${new Date(ticket.replied_at).toLocaleString()}` : ""}
          </label>
          <div
            style={{
              background: "rgba(22,163,74,0.06)",
              border: "1px solid rgba(22,163,74,0.2)",
              borderRadius: "8px",
              padding: "0.8rem",
              fontSize: "0.85rem",
              whiteSpace: "pre-wrap",
            }}
          >
            {ticket.reply_message}
          </div>
        </div>
      )}

      {ticket.status !== "answered" && (
        <div className="form-group">
          <label className="form-label">Your Reply</label>
          <textarea
            className="form-textarea"
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            placeholder="This will appear in the researcher's Contact Support dashboard…"
            rows={5}
          />
        </div>
      )}
    </Modal>
  );
}
