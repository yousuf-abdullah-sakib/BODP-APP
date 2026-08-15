"use client";

import { useState } from "react";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import { formatBytes, useUploadTracker } from "@/context/UploadTrackerContext";

/** The actual progress/cancel/minimize UI for one tracked upload — owned
 * by UploadTrackerContext (rendered whenever openDetailKey matches this
 * upload), not by whichever admin section originally opened the picker.
 * This is what makes minimize-and-keep-using-the-panel work: closing this
 * modal only clears openDetailKey, it never touches the underlying
 * transfer, which lives in the provider's own refs/state regardless of
 * whether this component is mounted. */
export default function UploadDetailModal({ localKey }: { localKey: string }) {
  const { toast } = useToast();
  const confirm = useConfirm();
  const { uploads, cancelTrackedUpload, dismissUpload, closeDetail } = useUploadTracker();
  const [cancelling, setCancelling] = useState(false);

  const tracked = uploads.find((u) => u.localKey === localKey);
  if (!tracked) return null;

  const uploading = tracked.status === "uploading" || tracked.status === "processing";

  function minimize() {
    closeDetail();
  }

  async function handleCancel() {
    const ok = await confirm({
      title: "Cancel Upload",
      message:
        "Cancel this upload? Any data already transferred will be discarded and no dataset file will be created.",
      confirmLabel: "Cancel Upload",
      danger: true,
    });
    if (!ok) return;

    setCancelling(true);
    try {
      await cancelTrackedUpload(localKey);
    } catch {
      toast("Failed to cancel upload.", "error");
    } finally {
      setCancelling(false);
    }
  }

  function handleCloseButton() {
    if (uploading) {
      // Closing (✕ / backdrop / Escape) during an active transfer
      // minimizes — it never implicitly cancels. Only the explicit
      // "Cancel Upload" button does that.
      minimize();
      return;
    }
    // Finished (complete/failed/cancelled) — closing here dismisses the
    // tracked entry so it doesn't linger in the indicator forever.
    dismissUpload(localKey);
  }

  return (
    <Modal
      title={`Uploading to "${tracked.datasetTitle}"`}
      onClose={handleCloseButton}
      footer={
        <>
          {uploading && (
            <button className="btn-ghost-sm" onClick={handleCancel} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel Upload"}
            </button>
          )}
          {uploading ? (
            <button className="btn-primary" onClick={minimize}>
              ⤢ Minimize
            </button>
          ) : (
            <button className="btn-primary" onClick={() => dismissUpload(localKey)}>
              Close
            </button>
          )}
        </>
      }
    >
      <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginBottom: "1rem" }}>
        {tracked.fileName}
      </p>

      {tracked.uploadPct !== null && (
        <div style={{ marginTop: "0.8rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "var(--text-muted)" }}>
            <span>Upload: {tracked.uploadPct}%</span>
            {tracked.totalBytes !== null && tracked.uploadedBytes !== null && (
              <span>
                {formatBytes(tracked.uploadedBytes)} / {formatBytes(tracked.totalBytes)}
              </span>
            )}
          </div>
          <div style={{ height: 6, background: "var(--border)", borderRadius: 3, marginTop: "0.3rem", overflow: "hidden" }}>
            <div
              style={{
                height: "100%",
                width: `${tracked.uploadPct}%`,
                background: "var(--accent)",
                transition: "width 0.2s",
              }}
            />
          </div>
        </div>
      )}

      {tracked.processingStage && (
        <div style={{ marginTop: "0.8rem" }}>
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
            Processing: {tracked.processingStage}
            {tracked.processingPct !== null ? ` ${tracked.processingPct}%` : "…"}
          </div>
          <div style={{ height: 6, background: "var(--border)", borderRadius: 3, marginTop: "0.3rem", overflow: "hidden" }}>
            <div
              style={{
                height: "100%",
                width: `${tracked.processingPct ?? 0}%`,
                background: "var(--accent-dark, var(--accent))",
                transition: "width 0.2s",
              }}
            />
          </div>
        </div>
      )}

      {tracked.statusText && (
        <div style={{ marginTop: "0.6rem", fontSize: "0.78rem", color: "var(--text-muted)" }}>{tracked.statusText}</div>
      )}

      {uploading && (
        <p style={{ marginTop: "1rem", fontSize: "0.76rem", color: "var(--text-muted)" }}>
          You can minimize this window and keep using the Admin Panel — the upload/processing job keeps running in
          the background. Click the indicator in the corner to reopen this view.
        </p>
      )}
    </Modal>
  );
}
