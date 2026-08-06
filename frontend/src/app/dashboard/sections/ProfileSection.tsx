"use client";

import { useEffect, useState } from "react";
import { useSession } from "@/context/SessionContext";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import AvatarUpload from "@/components/ui/AvatarUpload";
import {
  cancelDeletion,
  getProfile,
  requestDeletion,
  updateProfile,
} from "@/lib/api/me";
import { ApiError } from "@/lib/api/client";
import type { ProfileDetail } from "@/lib/types/me";

export default function ProfileSection() {
  const { user } = useSession();
  const { toast } = useToast();
  const confirm = useConfirm();

  const [profile, setProfile] = useState<ProfileDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [institution, setInstitution] = useState("");
  const [researchArea, setResearchArea] = useState("");
  const [bio, setBio] = useState("");

  useEffect(() => {
    let cancelled = false;
    getProfile()
      .then((data) => {
        if (cancelled) return;
        setProfile(data);
        setFullName(data.full_name);
        setPhone(data.phone ?? "");
        setInstitution(data.institution ?? "");
        setResearchArea(data.research_area ?? "");
        setBio(data.bio ?? "");
      })
      .catch(() => {
        if (!cancelled) toast("Failed to load your profile.", "error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  async function handleSave() {
    setSaving(true);
    try {
      const updated = await updateProfile({
        full_name: fullName,
        phone,
        institution,
        research_area: researchArea,
        bio,
      });
      setProfile(updated);
      toast("Profile updated.", "success");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to update profile.", "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteAccount() {
    const ok = await confirm({
      title: "Request Account Deletion",
      message:
        "Are you sure you want to request account deletion? Your account stays active for a 30-day grace period, after which your dataset access grants will be automatically revoked. You can cancel this request at any time before then.",
      confirmLabel: "Request Deletion",
      danger: true,
    });
    if (!ok) return;
    try {
      const status = await requestDeletion();
      setProfile((p) => (p ? { ...p, deletion_requested_at: status.deletion_requested_at } : p));
      toast("Deletion request submitted.", "info");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to submit deletion request.", "error");
    }
  }

  async function handleCancelDeletion() {
    try {
      await cancelDeletion();
      setProfile((p) => (p ? { ...p, deletion_requested_at: null } : p));
      toast("Deletion request cancelled.", "success");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to cancel deletion request.", "error");
    }
  }

  const initials = (user?.full_name ?? "User")
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  if (loading || !profile) {
    return (
      <>
        <div className="dash-header">
          <div>
            <div className="dash-title">Profile &amp; Details</div>
            <div className="dash-sub">Manage your personal and research information.</div>
          </div>
        </div>
        <div className="empty-state">
          <div className="es-icon">⏳</div>
          <p>Loading your profile…</p>
        </div>
      </>
    );
  }

  const deletionPending = !!profile.deletion_requested_at;
  const deletionDeadline = profile.deletion_requested_at
    ? new Date(new Date(profile.deletion_requested_at).getTime() + 30 * 24 * 60 * 60 * 1000)
    : null;

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Profile &amp; Details</div>
          <div className="dash-sub">Manage your personal and research information.</div>
        </div>
      </div>

      {deletionPending && (
        <div className="form-alert form-alert-error" style={{ marginBottom: "1.2rem" }}>
          Account deletion requested{deletionDeadline ? ` — grants will be revoked on ${deletionDeadline.toLocaleDateString()}` : ""}.{" "}
          <button className="panel-link" onClick={handleCancelDeletion}>
            Cancel deletion request
          </button>
        </div>
      )}

      <div className="panel-grid-2">
        <div style={{ display: "flex", flexDirection: "column", gap: "1.2rem" }}>
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Personal Information</span>
            </div>
            <div className="panel-body">
              <AvatarUpload initials={initials} />
              <div className="form-group">
                <label className="form-label">Full Name</label>
                <input
                  className="form-input"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Email (cannot be changed)</label>
                <input className="form-input" value={profile.email} disabled style={{ opacity: 0.6 }} />
              </div>
              <div className="form-group">
                <label className="form-label">Phone</label>
                <input
                  className="form-input"
                  placeholder="+880 1X XX XXX XXX"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Research &amp; Institution</span>
            </div>
            <div className="panel-body">
              <div className="form-group">
                <label className="form-label">Institution</label>
                <input
                  className="form-input"
                  value={institution}
                  onChange={(e) => setInstitution(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Research Area</label>
                <input
                  className="form-input"
                  placeholder="Coastal Oceanography, Water Quality…"
                  value={researchArea}
                  onChange={(e) => setResearchArea(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Bio</label>
                <textarea
                  className="form-textarea"
                  value={bio}
                  onChange={(e) => setBio(e.target.value)}
                  placeholder="Short professional bio…"
                />
              </div>
              <button className="btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? "Saving…" : "Save Changes"}
              </button>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "1.2rem" }}>
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">Account Status</span>
            </div>
            <div className="panel-body">
              <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", lineHeight: 1.8 }}>
                Datasets granted: <b>{profile.datasets_granted}</b>
                <br />
                Member since: {new Date(profile.created_at).toLocaleDateString(undefined, {
                  month: "short",
                  year: "numeric",
                })}
              </div>
            </div>
          </div>

          <div className="danger-zone">
            <div className="danger-zone-title">Danger Zone</div>
            <div className="danger-zone-desc">
              Requesting account deletion will revoke all dataset grants after a 30-day grace
              period. You can cancel the request at any time before then.
            </div>
            {!deletionPending && (
              <button className="btn-danger" onClick={handleDeleteAccount}>
                Request Account Deletion
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
