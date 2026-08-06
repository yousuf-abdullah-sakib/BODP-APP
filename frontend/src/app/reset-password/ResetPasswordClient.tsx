"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { confirmPasswordReset } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

export default function ResetPasswordClient({ token }: { token: string | null }) {
  const router = useRouter();
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError("");
    if (!token) {
      setError("This reset link is missing a token.");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await confirmPasswordReset(token, newPassword);
      setSuccess(true);
      setTimeout(() => router.push("/login"), 2000);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "This reset link is invalid or has expired."
      );
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") submit();
  }

  return (
    <div className="auth-wrapper" onKeyDown={onKeyDown}>
      <div className="auth-box">
        <Link className="back-link" href="/">
          ← Back to Home
        </Link>

        <div className="auth-brand">
          <div className="logo">BODP</div>
          <p>Reset your password</p>
        </div>

        {!token ? (
          <div className="auth-error">
            This reset link is missing a token. Please use the link from your password reset
            email.
          </div>
        ) : success ? (
          <div className="auth-success">Password reset. Redirecting you to sign in…</div>
        ) : (
          <div>
            {error && <div className="auth-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">New Password</label>
              <input
                className="form-input"
                type="password"
                placeholder="Min. 8 characters"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Confirm Password</label>
              <input
                className="form-input"
                type="password"
                placeholder="Re-enter password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </div>
            <button className="btn-submit" disabled={busy} onClick={submit}>
              {busy ? "Resetting…" : "Reset Password"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
