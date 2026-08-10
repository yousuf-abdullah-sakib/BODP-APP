"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { CONTACT_SUBJECT_LABELS } from "@/lib/types/admin-contact";
import type { ContactSubmissionAdminDetail } from "@/lib/types/admin-contact";

interface ContactReplyModalProps {
  submission: ContactSubmissionAdminDetail;
  onClose: () => void;
  onReply: (replyMessage: string) => Promise<void>;
}

export default function ContactReplyModal({ submission, onClose, onReply }: ContactReplyModalProps) {
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
      title="Contact Message"
      onClose={onClose}
      large
      footer={
        submission.status === "replied" ? (
          <button className="btn-ghost-sm" onClick={onClose}>
            Close
          </button>
        ) : (
          <>
            <button className="btn-ghost-sm" onClick={onClose}>
              Cancel
            </button>
            <button className="btn-primary-sm" onClick={submit} disabled={sending}>
              {sending ? "Sending…" : "✉ Send Reply"}
            </button>
          </>
        )
      }
    >
      <div className="form-group">
        <label className="form-label">From</label>
        <div>
          <b>{submission.name}</b> — {submission.email}
          {submission.organization && (
            <span style={{ color: "var(--text-muted)" }}> ({submission.organization})</span>
          )}
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">Subject</label>
        <div>{CONTACT_SUBJECT_LABELS[submission.subject] ?? submission.subject}</div>
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
          {submission.message}
        </div>
      </div>

      {submission.reply_message && (
        <div className="form-group">
          <label className="form-label">
            Reply sent {submission.replied_by_name ? `by ${submission.replied_by_name}` : ""}
            {submission.replied_at ? ` — ${new Date(submission.replied_at).toLocaleString()}` : ""}
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
            {submission.reply_message}
          </div>
        </div>
      )}

      {submission.status !== "replied" && (
        <div className="form-group">
          <label className="form-label">Your Reply</label>
          <textarea
            className="form-textarea"
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            placeholder="This will be emailed directly to the sender's address…"
            rows={5}
          />
        </div>
      )}
    </Modal>
  );
}
