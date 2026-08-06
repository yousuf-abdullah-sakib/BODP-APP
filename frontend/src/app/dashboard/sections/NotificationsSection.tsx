"use client";

import { useEffect, useState } from "react";
import {
  getNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "@/lib/api/me";
import type { NotificationSummary } from "@/lib/types/me";

const ICONS: Record<string, string> = { success: "✅", warning: "⚠️", info: "ℹ️", danger: "🚫" };

export default function NotificationsSection({
  onUnreadCountChange,
}: {
  onUnreadCountChange: (count: number) => void;
}) {
  const [items, setItems] = useState<NotificationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getNotifications()
      .then((data) => {
        if (!cancelled) setItems(data);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load notifications.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const unreadCount = items.filter((n) => n.unread).length;

  async function handleMarkRead(id: string) {
    const updated = await markNotificationRead(id);
    setItems((prev) => prev.map((n) => (n.id === id ? updated : n)));
    onUnreadCountChange(items.filter((n) => n.unread && n.id !== id).length);
  }

  async function handleMarkAllRead() {
    await markAllNotificationsRead();
    setItems((prev) => prev.map((n) => ({ ...n, unread: false })));
    onUnreadCountChange(0);
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Notifications</div>
          <div className="dash-sub">{unreadCount} unread notification{unreadCount === 1 ? "" : "s"}</div>
        </div>
        <button className="btn-outline" onClick={handleMarkAllRead} disabled={unreadCount === 0}>
          Mark all read
        </button>
      </div>

      {error ? (
        <div className="empty-state">
          <div className="es-icon">⚠️</div>
          <p>{error}</p>
        </div>
      ) : loading ? (
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading notifications…</p>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <div className="es-icon">🔔</div>
          <p>No notifications yet.</p>
        </div>
      ) : (
        <div className="panel">
          <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
            {items.map((n) => (
              <div
                key={n.id}
                className={`notif-item${n.unread ? " unread" : ""}`}
                onClick={() => n.unread && handleMarkRead(n.id)}
                style={{ cursor: n.unread ? "pointer" : "default" }}
              >
                <div className={`notif-icon ${n.type}`}>{ICONS[n.type] ?? "ℹ️"}</div>
                <div>
                  <div className="notif-title">{n.title}</div>
                  {n.description && <div className="notif-desc">{n.description}</div>}
                  <div className="notif-time">{new Date(n.created_at).toLocaleString()}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
