"use client";

import { useEffect, useState } from "react";
import { useToast } from "@/context/ToastContext";
import {
  getGeneralSettings,
  updateGeneralSettings,
  getNotificationSettings,
  updateNotificationSettings,
} from "@/lib/api/admin-settings";
import { getAdminTeam } from "@/lib/api/admin-team";
import { getSessions } from "@/lib/api/me";
import type {
  GeneralSettingsSchema,
  NotificationSettingsSchema,
} from "@/lib/types/admin-general-settings";
import type { AdminTeamMemberPublic } from "@/lib/types/admin-team";
import type { SessionSummary } from "@/lib/types/me";

export default function SettingsSection() {
  const { toast } = useToast();

  const [general, setGeneral] = useState<GeneralSettingsSchema | null>(null);
  const [notifications, setNotifications] = useState<NotificationSettingsSchema | null>(null);
  const [team, setTeam] = useState<AdminTeamMemberPublic[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingGeneral, setSavingGeneral] = useState(false);
  const [savingNotifications, setSavingNotifications] = useState(false);

  useEffect(() => {
    Promise.all([getGeneralSettings(), getNotificationSettings(), getAdminTeam(), getSessions()])
      .then(([g, n, t, s]) => {
        setGeneral(g);
        setNotifications(n);
        setTeam(t);
        setSessions(s);
      })
      .catch(() => toast("Failed to load settings.", "error"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateGeneralField<K extends keyof GeneralSettingsSchema>(
    key: K,
    value: GeneralSettingsSchema[K]
  ) {
    setGeneral((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function updateNotificationField<K extends keyof NotificationSettingsSchema>(
    key: K,
    value: NotificationSettingsSchema[K]
  ) {
    setNotifications((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function saveGeneral() {
    if (!general) return;
    setSavingGeneral(true);
    try {
      const saved = await updateGeneralSettings(general);
      setGeneral(saved);
      toast("Settings saved.", "success");
    } catch {
      toast("Failed to save settings.", "error");
    } finally {
      setSavingGeneral(false);
    }
  }

  async function saveNotifications() {
    if (!notifications) return;
    setSavingNotifications(true);
    try {
      const saved = await updateNotificationSettings(notifications);
      setNotifications(saved);
      toast("Notification triggers saved.", "success");
    } catch {
      toast("Failed to save notification triggers.", "error");
    } finally {
      setSavingNotifications(false);
    }
  }

  if (loading || !general || !notifications) {
    return (
      <>
        <div className="dash-header">
          <div>
            <div className="dash-title">Settings</div>
            <div className="dash-sub">Site configuration and administration.</div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-body">Loading settings…</div>
        </div>
      </>
    );
  }

  const activeAdmins = team.filter((m) => m.status === "active").length;
  const currentSession = sessions.find((s) => s.is_current) ?? null;
  const otherSessionCount = sessions.filter((s) => !s.is_current).length;

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Settings</div>
          <div className="dash-sub">Site configuration and administration.</div>
        </div>
      </div>

      <div className="panel-grid-2">
        <div style={{ display: "flex", flexDirection: "column", gap: "1.2rem" }}>
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Site Settings</span>
            </div>
            <div className="panel-body">
              <div className="form-group">
                <label className="form-label">Site Name</label>
                <input
                  className="form-input"
                  value={general.site_name}
                  onChange={(e) => updateGeneralField("site_name", e.target.value)}
                />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Contact Email</label>
                  <input
                    className="form-input"
                    value={general.contact_email ?? ""}
                    onChange={(e) => updateGeneralField("contact_email", e.target.value || null)}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Data Access Email</label>
                  <input
                    className="form-input"
                    value={general.data_access_email ?? ""}
                    onChange={(e) =>
                      updateGeneralField("data_access_email", e.target.value || null)
                    }
                  />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Max Upload Size (MB)</label>
                  <input
                    className="form-input"
                    type="number"
                    value={general.max_upload_size_mb}
                    onChange={(e) =>
                      updateGeneralField("max_upload_size_mb", Number(e.target.value))
                    }
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Session Lifetime (minutes)</label>
                  <input
                    className="form-input"
                    type="number"
                    value={general.session_lifetime_min}
                    onChange={(e) =>
                      updateGeneralField("session_lifetime_min", Number(e.target.value))
                    }
                  />
                </div>
              </div>
              <button className="btn-primary" onClick={saveGeneral} disabled={savingGeneral}>
                {savingGeneral ? "Saving…" : "Save Settings"}
              </button>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Email Notification Triggers</span>
            </div>
            <div className="panel-body">
              <div className="toggle-row">
                <div className="toggle-label">New dataset access request</div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={notifications.notify_new_request}
                    onChange={(e) => updateNotificationField("notify_new_request", e.target.checked)}
                  />
                  <span className="toggle-slider" />
                </label>
              </div>
              <div className="toggle-row">
                <div className="toggle-label">New user registration</div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={notifications.notify_new_user}
                    onChange={(e) => updateNotificationField("notify_new_user", e.target.checked)}
                  />
                  <span className="toggle-slider" />
                </label>
              </div>
              <div className="toggle-row">
                <div className="toggle-label">Dataset expiring soon</div>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={notifications.notify_expiring_dataset}
                    onChange={(e) =>
                      updateNotificationField("notify_expiring_dataset", e.target.checked)
                    }
                  />
                  <span className="toggle-slider" />
                </label>
              </div>
              <button
                className="btn-primary"
                onClick={saveNotifications}
                disabled={savingNotifications}
              >
                {savingNotifications ? "Saving…" : "Save Triggers"}
              </button>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "1.2rem" }}>
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Admin Team</span>
              <span className="chip">{activeAdmins} active / {team.length} total</span>
            </div>
            <div className="panel-body">
              {team.length === 0 ? (
                <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>
                  No administrators found.
                </div>
              ) : (
                team.map((m) => (
                  <div className="act-item" key={m.id}>
                    <div className="act-dot">👤</div>
                    <div>
                      <div className="act-text">
                        <b>{m.full_name}</b> — {m.roles.join(", ") || "Administrator"}
                        {m.status !== "active" && (
                          <span
                            className="badge badge-suspended"
                            style={{ marginLeft: "0.5rem", fontSize: "0.65rem" }}
                          >
                            {m.status}
                          </span>
                        )}
                      </div>
                      <div className="act-time">
                        {m.email}
                        {m.last_active_at
                          ? ` — last active ${new Date(m.last_active_at).toLocaleDateString()}`
                          : ""}
                      </div>
                    </div>
                  </div>
                ))
              )}
              <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "0.8rem" }}>
                Manage administrators, roles, and invitations from the dedicated Admin Management
                section.
              </p>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Your Session</span>
            </div>
            <div className="panel-body">
              {currentSession ? (
                <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", lineHeight: 1.8 }}>
                  Signed in from: <b>{currentSession.device ?? "Unknown device"}</b>
                  {currentSession.ip_address ? ` (${currentSession.ip_address})` : ""}
                  <br />
                  Session started: {new Date(currentSession.created_at).toLocaleString()}
                  <br />
                  Other active sessions: <b>{otherSessionCount}</b>
                </div>
              ) : (
                <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>
                  Session information unavailable.
                </div>
              )}
              <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "0.8rem" }}>
                Manage your password and review or revoke every active session from your Profile
                page.
              </p>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
