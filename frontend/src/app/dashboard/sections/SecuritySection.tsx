"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useToast } from "@/context/ToastContext";
import { useConfirm } from "@/context/ConfirmContext";
import { useSession } from "@/context/SessionContext";
import { changePassword, getSessions, revokeSession } from "@/lib/api/me";
import { ApiError } from "@/lib/api/client";
import type { SessionSummary } from "@/lib/types/me";

function strengthOf(pw: string): { label: string; level: number } {
  let score = 0;
  if (pw.length >= 8) score++;
  if (/[A-Z]/.test(pw)) score++;
  if (/[0-9]/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  if (score <= 1) return { label: "Weak", level: 1 };
  if (score === 2) return { label: "Fair", level: 2 };
  if (score === 3) return { label: "Good", level: 3 };
  return { label: "Strong", level: 4 };
}

export default function SecuritySection() {
  const router = useRouter();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { logout } = useSession();

  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [changingPw, setChangingPw] = useState(false);

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const strength = useMemo(() => strengthOf(newPw), [newPw]);
  const segClass = strength.level <= 1 ? "filled-weak" : strength.level <= 2 ? "filled-fair" : "filled-strong";

  useEffect(() => {
    let cancelled = false;
    getSessions()
      .then((data) => {
        if (!cancelled) setSessions(data);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoadingSessions(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleChangePassword() {
    if (!currentPw || !newPw) {
      toast("Please fill in both password fields.", "error");
      return;
    }
    const ok = await confirm({
      title: "Change Password",
      message:
        "Changing your password will sign you out of every other device you're logged in on. Continue?",
      confirmLabel: "Change Password",
      danger: false,
    });
    if (!ok) return;

    setChangingPw(true);
    try {
      await changePassword(currentPw, newPw);
      toast("Password updated. Please sign back in.", "success");
      await logout();
      router.push("/login");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to change password.", "error");
    } finally {
      setChangingPw(false);
    }
  }

  async function handleRevoke(session: SessionSummary) {
    const ok = await confirm({
      title: "Revoke Session",
      message: (
        <>
          Revoke this session? <b>{session.device ?? "This device"}</b> will be signed out
          immediately.
        </>
      ),
      confirmLabel: "Revoke",
      danger: true,
    });
    if (!ok) return;

    setRevokingId(session.id);
    try {
      await revokeSession(session.id);
      setSessions((prev) => prev.filter((s) => s.id !== session.id));
      toast("Session revoked.", "success");
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "Failed to revoke session.", "error");
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <>
      <div className="dash-header">
        <div>
          <div className="dash-title">Security</div>
          <div className="dash-sub">Manage your password and active sessions.</div>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1.2rem" }}>
        <div className="panel-head">
          <span className="panel-title">Change Password</span>
        </div>
        <div className="panel-body">
          <div className="form-group">
            <label className="form-label">Current Password</label>
            <input
              className="form-input"
              type="password"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
            />
          </div>
          <div className="form-group">
            <label className="form-label">New Password</label>
            <input
              className="form-input"
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
            />
            <div className="pw-strength-bar">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className={`pw-seg${i <= strength.level && newPw ? " " + segClass : ""}`} />
              ))}
            </div>
            {newPw && <div className="pw-strength-label">Strength: {strength.label}</div>}
          </div>
          <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginBottom: "0.8rem" }}>
            Changing your password signs you out of every device, including this one.
          </div>
          <button className="btn-primary" onClick={handleChangePassword} disabled={changingPw}>
            {changingPw ? "Updating…" : "Update Password"}
          </button>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1.2rem" }}>
        <div className="panel-head">
          <span className="panel-title">Active Sessions</span>
        </div>
        <div className="panel-body">
          {loadingSessions ? (
            <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>Loading sessions…</div>
          ) : sessions.length === 0 ? (
            <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>No active sessions.</div>
          ) : (
            sessions.map((s) => (
              <div className="session-item" key={s.id}>
                <div>
                  <div className="session-device">
                    {s.device ?? "Unknown device"}
                    {s.ip_address ? ` — ${s.ip_address}` : ""}
                  </div>
                  <div className="session-meta">
                    Last active {new Date(s.last_active_at).toLocaleString()}
                  </div>
                </div>
                {s.is_current ? (
                  <span className="session-current">Current</span>
                ) : (
                  <button
                    className="btn-cancel"
                    onClick={() => handleRevoke(s)}
                    disabled={revokingId === s.id}
                  >
                    {revokingId === s.id ? "Revoking…" : "Revoke"}
                  </button>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">Two-Factor Authentication</span>
        </div>
        <div className="panel-body">
          <div style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>
            2FA is coming in v2.1. You&apos;ll be able to secure your account with an
            authenticator app.
          </div>
        </div>
      </div>
    </>
  );
}
