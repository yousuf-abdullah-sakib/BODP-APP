"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { ApiError } from "@/lib/api/client";
import { getDetailedHealth } from "@/lib/api/admin-health";
import type { DetailedHealthResponse } from "@/lib/types/admin-health";

const POLL_INTERVAL_MS = 15000;

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function HealthRow({ label, healthy, detail }: { label: string; healthy: boolean; detail?: string }) {
  return (
    <div className="health-row">
      <span>
        <span className={`health-dot ${healthy ? "healthy" : "bad"}`} />
        {label}
      </span>
      <span style={{ color: healthy ? "var(--green)" : "var(--red)", fontSize: "0.8rem" }}>
        {detail ?? (healthy ? "Healthy" : "Unavailable")}
      </span>
    </div>
  );
}

export default function SystemHealthSection() {
  const { toast } = useToast();
  const [health, setHealth] = useState<DetailedHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  function refetch() {
    setLoading(true);
    getDetailedHealth()
      .then((data) => {
        setHealth(data);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "Failed to load system health.");
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
    pollTimer.current = setInterval(refetch, POLL_INTERVAL_MS);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleManualRefresh() {
    refetch();
    toast("Refreshed.", "success");
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">System Health</div>
          <div className="dash-sub">
            Live status of every subsystem the platform depends on — checked every 15 seconds.
          </div>
        </div>
        <button className="btn-outline" onClick={handleManualRefresh} disabled={loading}>
          {loading ? "Checking…" : "🔄 Refresh Now"}
        </button>
      </div>

      {error && !health ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : !health ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Checking system health…</p>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "1rem" }}>
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Database</span>
            </div>
            <div className="panel-body">
              <HealthRow label="Postgres" healthy={health.database.healthy} detail={health.database.error ?? undefined} />
              {health.database.healthy && (
                <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
                  Pool: {health.database.pool_checked_out} / {health.database.pool_size} connections in use
                </div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Redis</span>
            </div>
            <div className="panel-body">
              <HealthRow label="Redis" healthy={health.redis.healthy} detail={health.redis.error ?? undefined} />
              {health.redis.healthy && health.redis.used_memory_bytes !== null && (
                <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
                  Memory used: {formatBytes(health.redis.used_memory_bytes)}
                </div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Storage</span>
            </div>
            <div className="panel-body">
              {health.storage.map((s) => (
                <HealthRow key={s.name} label={s.name} healthy={s.healthy} detail={s.error ?? undefined} />
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Celery Workers</span>
            </div>
            <div className="panel-body">
              {health.celery.error && !health.celery.healthy && health.celery.queues.every((q) => !q.healthy) ? (
                <HealthRow label="Celery" healthy={false} detail={health.celery.error} />
              ) : (
                health.celery.queues.map((q) => (
                  <HealthRow
                    key={q.queue}
                    label={`${q.queue} queue`}
                    healthy={q.healthy}
                    detail={q.healthy ? `${q.worker_count} worker(s)` : "No workers"}
                  />
                ))
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Disk</span>
            </div>
            <div className="panel-body">
              <HealthRow
                label="Disk usage"
                healthy={health.disk.percent_used < 90}
                detail={`${health.disk.percent_used}% used`}
              />
              <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
                {formatBytes(health.disk.used_bytes)} used of {formatBytes(health.disk.total_bytes)} (
                {formatBytes(health.disk.free_bytes)} free)
              </div>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.4rem" }}>
                Approximate inside a container — VPS-level disk monitoring is the authoritative source.
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Last Backup</span>
            </div>
            <div className="panel-body">
              {health.last_backup ? (
                <HealthRow
                  label={new Date(health.last_backup.started_at).toLocaleString()}
                  healthy={health.last_backup.status === "success"}
                  detail={
                    health.last_backup.status === "success"
                      ? "Ready"
                      : health.last_backup.status === "running"
                        ? "Running…"
                        : "Failed"
                  }
                />
              ) : (
                <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                  No backups yet — see Backups &amp; Recovery.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {health && (
        <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "1rem" }}>
          Checked {new Date(health.checked_at).toLocaleString()}
        </div>
      )}
    </>
  );
}
