"use client";

import Modal from "@/components/ui/Modal";
import CategoryPill from "@/components/ui/CategoryPill";
import DownloadButton from "./DownloadButton";
import { useExtractionDownload } from "./useExtractionDownload";
import type { GrantSummary } from "@/lib/types/requests";

interface DatasetDetailModalProps {
  grant: GrantSummary;
  onClose: () => void;
}

export default function DatasetDetailModal({ grant, onClose }: DatasetDetailModalProps) {
  const { downloadingGrantId, download } = useExtractionDownload();

  return (
    <Modal
      title="Dataset Details"
      onClose={onClose}
      footer={
        <>
          <button className="btn-cancel" onClick={onClose}>
            Close
          </button>
          <DownloadButton
            grant={grant}
            busy={downloadingGrantId === grant.id}
            onDownload={download}
            buttonClassName="btn-submit"
          />
        </>
      }
    >
      <div style={{ marginBottom: "1rem" }}>
        <CategoryPill category={grant.dataset.category} />
        <div style={{ fontWeight: 700, fontSize: "1rem", color: "var(--text-primary)", marginTop: "0.5rem" }}>
          {grant.dataset.title}
        </div>
        <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginTop: "0.15rem" }}>{grant.dataset.code}</div>
      </div>

      <div className="req-dataset-list">
        <div className="req-dataset-row">
          <span>📅 Granted</span>
          <span>{new Date(grant.granted_at).toLocaleDateString()}</span>
        </div>
        <div className="req-dataset-row">
          <span>⏳ Access Expires</span>
          <span>{new Date(grant.expires_at).toLocaleDateString()}</span>
        </div>
        <div className="req-dataset-row">
          <span>Status</span>
          <span>{grant.status}</span>
        </div>
      </div>
    </Modal>
  );
}
