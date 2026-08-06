"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { fetchCurrentUser, login as apiLogin, logout as apiLogout } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import type { SessionUser } from "@/lib/types/user";

interface SessionContextValue {
  user: SessionUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<SessionUser>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | undefined>(undefined);

const ACCESS_TOKEN_KEY = "bodp_access_token";
const REFRESH_TOKEN_KEY = "bodp_refresh_token";

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function hydrate() {
      const token = window.localStorage.getItem(ACCESS_TOKEN_KEY);
      if (!token) {
        if (!cancelled) setLoading(false);
        return;
      }
      try {
        const me = await fetchCurrentUser();
        if (!cancelled) setUser(me);
      } catch (err) {
        // Expired/invalid token — clear it rather than leaving a stale
        // session that will 401 on every subsequent authenticated call.
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          window.localStorage.removeItem(ACCESS_TOKEN_KEY);
          window.localStorage.removeItem(REFRESH_TOKEN_KEY);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    hydrate();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await apiLogin(email, password);
    window.localStorage.setItem(ACCESS_TOKEN_KEY, result.tokens.access_token);
    window.localStorage.setItem(REFRESH_TOKEN_KEY, result.tokens.refresh_token);
    setUser(result.user);
    return result.user;
  }, []);

  const logout = useCallback(async () => {
    const refreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY);
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
    setUser(null);
    if (refreshToken) {
      try {
        await apiLogout(refreshToken);
      } catch {
        // Best-effort server-side revocation — local session is already
        // cleared either way, so a network failure here shouldn't block logout.
      }
    }
  }, []);

  return (
    <SessionContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used within a SessionProvider");
  return ctx;
}
