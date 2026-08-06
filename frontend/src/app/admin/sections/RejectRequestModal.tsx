"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";

interface RejectRequestModalProps {
  onClose: () => void;
  onConfirm: (reason: string) => void;
}

export default function RejectRequestModal({ onClose, onConfirm }: RejectRequestModalProps) {
  const { toast } = useToast();
  const [reason, setReason] = useState("");

  function submit() {
    if (!reason.trim()) {
      toast("Please provide a reason for rejection.", "error");
      return;
    }
    onConfirm(reason.trim());
  }

  return (
    <Modal
      title="Reject Request"
      onClose={onClose}
      footer={
        <>
          <button className="btn-ghost-sm" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-danger-sm" onClick={submit}>
            ✗ Reject Request
          </button>
        </>
      }
    >
      <div className="form-group">
        <label className="form-label">
          Reason for rejection <span style={{ color: "var(--red)" }}>*</span>
        </label>
        <textarea
          className="form-textarea"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="This will be shown to the researcher, e.g. 'Justification letter did not sufficiently describe intended use.'"
        />
      </div>
    </Modal>
  );
}
