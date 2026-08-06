import { apiFetch } from "./client";
import type { SessionUser } from "@/lib/types/user";

interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

interface LoginResponse {
  user: SessionUser;
  tokens: TokenPair;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/auth/login", {
    method: "POST",
    body: { email, password },
    skipAuth: true,
  });
}

export interface RegisterPayload {
  full_name: string;
  email: string;
  password: string;
  institution?: string | null;
  phone?: string | null;
}

export async function register(payload: RegisterPayload): Promise<{ id: string; email: string; message: string }> {
  return apiFetch("/auth/register", { method: "POST", body: payload, skipAuth: true });
}

export async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>("/auth/me");
}

export async function logout(refreshToken: string): Promise<void> {
  await apiFetch("/auth/logout", {
    method: "POST",
    body: { refresh_token: refreshToken },
    skipAuth: true,
  });
}

export async function verifyEmail(token: string): Promise<SessionUser> {
  return apiFetch<SessionUser>("/auth/verify-email", {
    method: "POST",
    body: { token },
    skipAuth: true,
  });
}

export async function resendVerification(email: string): Promise<void> {
  await apiFetch("/auth/resend-verification", { method: "POST", body: { email }, skipAuth: true });
}

export async function requestPasswordReset(email: string): Promise<void> {
  await apiFetch("/auth/password-reset/request", {
    method: "POST",
    body: { email },
    skipAuth: true,
  });
}

export async function confirmPasswordReset(token: string, newPassword: string): Promise<void> {
  await apiFetch("/auth/password-reset/confirm", {
    method: "POST",
    body: { token, new_password: newPassword },
    skipAuth: true,
  });
}

export async function confirmInvite(token: string, newPassword: string): Promise<void> {
  await apiFetch("/auth/confirm-invite", {
    method: "POST",
    body: { token, new_password: newPassword },
    skipAuth: true,
  });
}
