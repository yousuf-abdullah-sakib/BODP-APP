"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { verifyEmail } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

type Status = "verifying" | "success" | "error";

export default function VerifyEmailClient({ token }: { token: string | null }) {
  const [status, setStatus] = useState<Status>("verifying");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setMessage("This verification link is missing a token.");
      return;
    }
    verifyEmail(token)
      .then(() => setStatus("success"))
      .catch((err) => {
        setStatus("error");
        setMessage(
          err instanceof ApiError
            ? err.message
            : "This verification link is invalid or has expired."
        );
      });
  }, [token]);

  return (
    <div className="auth-wrapper">
      <div className="auth-box">
        <Link className="back-link" href="/">
          ← Back to Home
        </Link>

        <div className="auth-brand">
          <div className="logo">BODP</div>
          <p>Bangladesh Oceanographic Data Portal</p>
        </div>

        {status === "verifying" && <p style={{ textAlign: "center" }}>Verifying your email…</p>}

        {status === "success" && (
          <>
            <div className="auth-success">Your email has been verified. You can now sign in.</div>
            <Link className="btn-submit" href="/login" style={{ display: "block", textAlign: "center" }}>
              Go to Sign In
            </Link>
          </>
        )}

        {status === "error" && (
          <>
            <div className="auth-error">{message}</div>
            <Link className="btn-submit" href="/login" style={{ display: "block", textAlign: "center" }}>
              Back to Sign In
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
