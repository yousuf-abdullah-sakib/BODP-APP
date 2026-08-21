"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { createBackup, getBackup, getBackupDownloadUrl, getBackups } from "@/lib/api/admin-backups";
import type { BackupPublic } from "@/lib/types/admin-backups";

const POLL_INTERVAL_MS = 2000;
const POLL_MAX_ATTEMPTS = 60;

function formatSize(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function statusBadgeClass(status: string): string {
  if (status === "success") return "badge badge-approved";
  if (status === "failed") return "badge badge-rejected";
  return "badge badge-pending";
}

function statusLabel(status: string): string {
  if (status === "success") return "Ready";
  if (status === "failed") return "Failed";
  return "Running…";
}

export default function BackupRecoverySection() {
  const { toast } = useToast();
  const [backups, setBackups] = useState<BackupPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function refetch() {
    setLoading(true);
    getBackups()
      .then(setBackups)
      .catch(() => toast("Failed to load backups.", "error"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pollUntilDone(backupId: string, attempt = 0) {
    if (attempt >= POLL_MAX_ATTEMPTS) return;
    pollTimer.current = setTimeout(async () => {
      try {
        const backup = await getBackup(backupId);
        setBackups((prev) => prev.map((b) => (b.id === backupId ? backup : b)));
        if (backup.status === "running") pollUntilDone(backupId, attempt + 1);
      } catch {
        // stop polling silently on error — the row still shows in the table
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleRunNow() {
    setStarting(true);
    try {
      const backup = await createBackup();
      toast("Backup started.", "success");
      refetch();
      pollUntilDone(backup.id);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to start backup.", "error");
    } finally {
      setStarting(false);
    }
  }

  async function handleDownload(backup: BackupPublic) {
    if (!backup.ready) {
      toast("Backup is still running — try again shortly.", "info");
      return;
    }
    try {
      const { download_url } = await getBackupDownloadUrl(backup.id);
      window.location.href = download_url;
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to get download link.", "error");
    }
  }

  const latest = backups[0];

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Backups &amp; Recovery</div>
          <div className="dash-sub">
            Real pg_dump snapshots of the production database, taken nightly and on demand.
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1.4rem" }}>
        <div className="panel-head">
          <span className="panel-title">Run a Backup Now</span>
        </div>
        <div className="panel-body">
          <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "1rem" }}>
            A scheduled backup already runs automatically every night. Use this only when you need a
            fresh snapshot right now — before a risky migration or a major data change, for example.
          </p>
          <button className="btn-primary" onClick={handleRunNow} disabled={starting}>
            {starting ? "Starting…" : "💾 Run Backup Now"}
          </button>
          {latest && (
            <div
              style={{
                marginTop: "1rem",
                padding: "0.8rem 1rem",
                borderRadius: 8,
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                fontSize: "0.82rem",
                color: "var(--text-secondary)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "1rem",
              }}
            >
              <span>
                Latest: <b>{new Date(latest.started_at).toLocaleString()}</b> —{" "}
                {statusLabel(latest.status)}
                {latest.status === "failed" && latest.error_message ? ` (${latest.error_message})` : ""}
              </span>
              <button
                className="btn-icon-sm"
                title="Download"
                onClick={() => handleDownload(latest)}
                disabled={!latest.ready}
              >
                ⬇
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Backup History</span>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          {loading ? (
            <div className="empty-state">
              <div className="es-icon">⏳</div>
              <p>Loading backups…</p>
            </div>
          ) : backups.length === 0 ? (
            <div className="empty-state">
              <div className="es-icon">💾</div>
              <p>No backups yet — run one now, or wait for tonight&apos;s scheduled backup.</p>
            </div>
          ) : (
            <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Completed</th>
                    <th>Size</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {backups.map((b) => (
                    <tr key={b.id}>
                      <td style={{ fontSize: "0.82rem" }}>{new Date(b.started_at).toLocaleString()}</td>
                      <td style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                        {b.completed_at ? new Date(b.completed_at).toLocaleString() : "—"}
                      </td>
                      <td style={{ fontSize: "0.82rem" }}>{formatSize(b.size_bytes)}</td>
                      <td>
                        <span
                          className={statusBadgeClass(b.status)}
                          title={b.status === "failed" ? (b.error_message ?? undefined) : undefined}
                        >
                          {statusLabel(b.status)}
                        </span>
                      </td>
                      <td>
                        <button
                          className="btn-icon-sm"
                          title="Download"
                          onClick={() => handleDownload(b)}
                          disabled={!b.ready}
                        >
                          ⬇
                        </button>
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
