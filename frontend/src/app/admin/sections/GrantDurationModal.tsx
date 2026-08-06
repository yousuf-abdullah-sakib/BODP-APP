"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import type { GrantDuration } from "@/lib/types/requests";

const OPTIONS: { key: GrantDuration; label: string }[] = [
  { key: "5d", label: "5 Days" },
  { key: "10d", label: "10 Days" },
  { key: "1m", label: "1 Month" },
  { key: "2m", label: "2 Months" },
  { key: "6m", label: "6 Months" },
  { key: "1y", label: "1 Year" },
  { key: "custom", label: "Custom Date Range" },
];

interface GrantDurationModalProps {
  title?: string;
  onClose: () => void;
  onConfirm: (duration: GrantDuration, customDate?: string) => void;
}

export default function GrantDurationModal({ title, onClose, onConfirm }: GrantDurationModalProps) {
  const [duration, setDuration] = useState<GrantDuration>("1y");
  const [customDate, setCustomDate] = useState("");

  function confirm() {
    if (duration === "custom" && !customDate) return;
    onConfirm(duration, duration === "custom" ? customDate : undefined);
  }

  return (
    <Modal
      title={title ?? "Set Access Duration"}
      onClose={onClose}
      footer={
        <>
          <button className="btn-ghost-sm" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-success-sm" onClick={confirm} disabled={duration === "custom" && !customDate}>
            ✓ Confirm
          </button>
        </>
      }
    >
      <div className="mini-label">Access Validity Period</div>
      <div className="duration-option-grid">
        {OPTIONS.map((o) => (
          <button
            key={o.key}
            type="button"
            className={`duration-option${duration === o.key ? " active" : ""}`}
            onClick={() => setDuration(o.key)}
          >
            {o.label}
          </button>
        ))}
      </div>
      {duration === "custom" && (
        <div className="form-group" style={{ marginTop: "1rem" }}>
          <label className="form-label">Expires On</label>
          <input type="date" className="form-input" value={customDate} onChange={(e) => setCustomDate(e.target.value)} />
        </div>
      )}
    </Modal>
  );
}
