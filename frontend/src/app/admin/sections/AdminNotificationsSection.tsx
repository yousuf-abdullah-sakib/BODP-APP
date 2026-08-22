"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import { getNotifications, markAllNotificationsRead, markNotificationRead } from "@/lib/api/me";
import type { NotificationSummary } from "@/lib/types/me";

const ICONS: Record<string, string> = { success: "✅", warning: "⚠️", info: "ℹ️", danger: "🚫" };

export default function AdminNotificationsSection({ onMutate }: { onMutate: () => void }) {
  const { toast } = useToast();
  const [items, setItems] = useState<NotificationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function refetch() {
    setLoading(true);
    getNotifications()
      .then(setItems)
      .catch(() => setError("Failed to load notifications."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refetch();
  }, []);

  const unreadCount = items.filter((n) => n.unread).length;

  async function handleMarkAllRead() {
    try {
      await markAllNotificationsRead();
      refetch();
      onMutate();
    } catch {
      toast("Failed to mark all notifications as read.", "error");
    }
  }

  async function handleItemClick(n: NotificationSummary) {
    if (!n.unread) return;
    try {
      await markNotificationRead(n.id);
      refetch();
      onMutate();
    } catch {
      toast("Failed to mark notification as read.", "error");
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Notifications</div>
          <div className="dash-sub">{unreadCount} unread notifications</div>
        </div>
        <button className="btn-outline" onClick={handleMarkAllRead}>
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
                onClick={() => handleItemClick(n)}
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
