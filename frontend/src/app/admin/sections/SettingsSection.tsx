"use client";

import { useEffect, useState } from "react";
import { useSession } from "@/context/SessionContext";
import { useToast } from "@/context/ToastContext";
import AvatarUpload from "@/components/ui/AvatarUpload";
import {
  getGeneralSettings,
  updateGeneralSettings,
  getNotificationSettings,
  updateNotificationSettings,
} from "@/lib/api/admin-settings";
import { getAdminTeam } from "@/lib/api/admin-team";
import { updateProfile, changePassword } from "@/lib/api/me";
import type {
  GeneralSettingsSchema,
  NotificationSettingsSchema,
} from "@/lib/types/admin-general-settings";
import type { AdminTeamMemberPublic } from "@/lib/types/admin-team";

function initialsOf(name: string): string {
  return name
    .split(" ")
    .map((w) => w[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export default function SettingsSection() {
  const { toast } = useToast();
  const { user, refreshUser } = useSession();

  const [general, setGeneral] = useState<GeneralSettingsSchema | null>(null);
  const [notifications, setNotifications] = useState<NotificationSettingsSchema | null>(null);
  const [team, setTeam] = useState<AdminTeamMemberPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingGeneral, setSavingGeneral] = useState(false);
  const [savingNotifications, setSavingNotifications] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);

  const [profileName, setProfileName] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");

  useEffect(() => {
    Promise.all([getGeneralSettings(), getNotificationSettings(), getAdminTeam()])
      .then(([g, n, t]) => {
        setGeneral(g);
        setNotifications(n);
        setTeam(t);
      })
      .catch(() => toast("Failed to load settings.", "error"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (user) setProfileName(user.full_name);
  }, [user]);

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

  async function saveProfile() {
    setSavingProfile(true);
    try {
      if (profileName.trim() && profileName !== user?.full_name) {
        await updateProfile({ full_name: profileName.trim() });
      }
      if (newPassword) {
        if (!currentPassword) {
          toast("Enter your current password to set a new one.", "error");
          setSavingProfile(false);
          return;
        }
        await changePassword(currentPassword, newPassword);
      }
      await refreshUser();
      toast("Admin profile updated.", "success");
      setCurrentPassword("");
      setNewPassword("");
    } catch {
      toast("Failed to update profile.", "error");
    } finally {
      setSavingProfile(false);
    }
  }

  if (loading || !general || !notifications) {
    return (
      <>
        <div className="dash-header">
          <div>
            <div className="dash-title">Settings</div>
            <div className="dash-sub">Site configuration and admin profile.</div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-body">Loading settings…</div>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Settings</div>
          <div className="dash-sub">Site configuration and admin profile.</div>
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
              <span className="panel-title">Admin Profile</span>
            </div>
            <div className="panel-body">
              <AvatarUpload variant="admin" initials={initialsOf(user?.full_name ?? "Admin")} />
              <div className="form-group">
                <label className="form-label">Name</label>
                <input
                  className="form-input"
                  value={profileName}
                  onChange={(e) => setProfileName(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Email</label>
                <input className="form-input" value={user?.email ?? ""} disabled />
              </div>
              <div className="form-group">
                <label className="form-label">Current Password</label>
                <input
                  className="form-input"
                  type="password"
                  placeholder="Required to set a new password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">New Password</label>
                <input
                  className="form-input"
                  type="password"
                  placeholder="Leave blank to keep current password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                />
              </div>
              <button className="btn-primary" onClick={saveProfile} disabled={savingProfile}>
                {savingProfile ? "Saving…" : "Save Profile"}
              </button>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Admin Team</span>
              <span className="chip">{team.length} member(s)</span>
            </div>
            <div className="panel-body">
              {team.slice(0, 4).map((m) => (
                <div className="act-item" key={m.id}>
                  <div className="act-dot">👤</div>
                  <div>
                    <div className="act-text">
                      <b>{m.full_name}</b> — {m.roles.join(", ") || "Administrator"}
                    </div>
                    <div className="act-time">{m.email}</div>
                  </div>
                </div>
              ))}
              <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "0.8rem" }}>
                Manage administrators, roles, and invitations from the dedicated Admin Management
                section.
              </p>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
