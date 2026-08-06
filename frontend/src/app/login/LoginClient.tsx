"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "@/context/SessionContext";
import { register as apiRegister } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";

export default function LoginClient() {
  const router = useRouter();
  const { login } = useSession();
  const [tab, setTab] = useState<"login" | "register">("login");

  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [institution, setInstitution] = useState("");
  const [phone, setPhone] = useState("");
  const [regError, setRegError] = useState("");
  const [regSuccess, setRegSuccess] = useState("");
  const [regBusy, setRegBusy] = useState(false);

  async function doLogin() {
    setLoginError("");
    if (!loginEmail.trim() || !loginPassword) {
      setLoginError("Please fill in all fields.");
      return;
    }
    setLoginBusy(true);
    try {
      const user = await login(loginEmail.trim(), loginPassword);
      router.push(user.role === "admin" ? "/admin" : "/dashboard");
    } catch (err) {
      setLoginError(err instanceof ApiError ? err.message : "Unable to sign in. Please try again.");
    } finally {
      setLoginBusy(false);
    }
  }

  async function doRegister() {
    setRegError("");
    setRegSuccess("");
    if (!firstName.trim() || !lastName.trim() || !regEmail.trim() || !regPassword) {
      setRegError("Name, email and password are required.");
      return;
    }
    if (regPassword.length < 8) {
      setRegError("Password must be at least 8 characters.");
      return;
    }
    setRegBusy(true);
    try {
      const result = await apiRegister({
        full_name: `${firstName.trim()} ${lastName.trim()}`,
        email: regEmail.trim(),
        password: regPassword,
        institution: institution.trim() || null,
        phone: phone.trim() || null,
      });
      setRegSuccess(result.message);
      setTimeout(() => setTab("login"), 2000);
    } catch (err) {
      setRegError(err instanceof ApiError ? err.message : "Unable to register. Please try again.");
    } finally {
      setRegBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key !== "Enter") return;
    if (tab === "register") doRegister();
    else doLogin();
  }

  return (
    <div className="auth-wrapper" onKeyDown={onKeyDown}>
      <div className="auth-box">
        <Link className="back-link" href="/">
          ← Back to Home
        </Link>

        <div className="auth-brand">
          <div className="logo">BODP</div>
          <p>Bangladesh Oceanographic Data Portal</p>
        </div>

        <div className="auth-tabs">
          <button
            className={`auth-tab${tab === "login" ? " active" : ""}`}
            onClick={() => setTab("login")}
          >
            Sign In
          </button>
          <button
            className={`auth-tab${tab === "register" ? " active" : ""}`}
            onClick={() => setTab("register")}
          >
            Register
          </button>
        </div>

        {tab === "login" ? (
          <div>
            {loginError && <div className="auth-error">{loginError}</div>}
            <div className="form-group">
              <label className="form-label">Email Address</label>
              <input
                className="form-input"
                type="email"
                placeholder="your@email.com"
                value={loginEmail}
                onChange={(e) => setLoginEmail(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <input
                className="form-input"
                type="password"
                placeholder="••••••••"
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
              />
            </div>
            <button className="btn-submit" disabled={loginBusy} onClick={doLogin}>
              {loginBusy ? "Signing in…" : "Sign In"}
            </button>
            <div className="auth-footer">
              Don&apos;t have an account?{" "}
              <a onClick={() => setTab("register")}>Register here</a>
            </div>
          </div>
        ) : (
          <div>
            {regError && <div className="auth-error">{regError}</div>}
            {regSuccess && <div className="auth-success">{regSuccess}</div>}
            <div className="name-row">
              <div className="form-group">
                <label className="form-label">First Name</label>
                <input
                  className="form-input"
                  type="text"
                  placeholder="Rahim"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Last Name</label>
                <input
                  className="form-input"
                  type="text"
                  placeholder="Uddin"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Email Address</label>
              <input
                className="form-input"
                type="email"
                placeholder="your@email.com"
                value={regEmail}
                onChange={(e) => setRegEmail(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <input
                className="form-input"
                type="password"
                placeholder="Min. 8 characters"
                value={regPassword}
                onChange={(e) => setRegPassword(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Institution / Organization</label>
              <input
                className="form-input"
                type="text"
                placeholder="BUET, CUET, DOE…"
                value={institution}
                onChange={(e) => setInstitution(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Phone (optional)</label>
              <input
                className="form-input"
                type="tel"
                placeholder="+880 1X XX XXX XXX"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
            <button className="btn-submit" disabled={regBusy} onClick={doRegister}>
              {regBusy ? "Creating account…" : "Create Account"}
            </button>
            <div className="auth-footer">
              Already have an account? <a onClick={() => setTab("login")}>Sign in</a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
