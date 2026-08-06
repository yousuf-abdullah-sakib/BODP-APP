import { apiFetch } from "./client";
import type {
  CmsBlockSummary,
  DeletionStatusResponse,
  NotificationSummary,
  OverviewResponse,
  PreferencesSchema,
  ProfileDetail,
  ProfileUpdate,
  SessionSummary,
  SupportTicketCreate,
  SupportTicketSummary,
  UnreadCountResponse,
} from "@/lib/types/me";

export async function getOverview(): Promise<OverviewResponse> {
  return apiFetch<OverviewResponse>("/me/overview");
}

export async function getProfile(): Promise<ProfileDetail> {
  return apiFetch<ProfileDetail>("/me/profile");
}

export async function updateProfile(data: ProfileUpdate): Promise<ProfileDetail> {
  return apiFetch<ProfileDetail>("/me/profile", { method: "PATCH", body: data });
}

export async function uploadAvatar(file: File): Promise<{ avatar_key: string }> {
  const form = new FormData();
  form.set("file", file);
  return apiFetch<{ avatar_key: string }>("/me/profile/avatar", { method: "POST", body: form });
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await apiFetch("/me/change-password", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

export async function getSessions(): Promise<SessionSummary[]> {
  return apiFetch<SessionSummary[]>("/me/sessions");
}

export async function revokeSession(sessionId: string): Promise<void> {
  await apiFetch(`/me/sessions/${sessionId}`, { method: "DELETE" });
}

export async function requestDeletion(): Promise<DeletionStatusResponse> {
  return apiFetch<DeletionStatusResponse>("/me/request-deletion", { method: "POST" });
}

export async function cancelDeletion(): Promise<DeletionStatusResponse> {
  return apiFetch<DeletionStatusResponse>("/me/cancel-deletion", { method: "POST" });
}

export async function getPreferences(): Promise<PreferencesSchema> {
  return apiFetch<PreferencesSchema>("/me/preferences");
}

export async function updatePreferences(data: PreferencesSchema): Promise<PreferencesSchema> {
  return apiFetch<PreferencesSchema>("/me/preferences", { method: "PATCH", body: data });
}

export async function getNotifications(): Promise<NotificationSummary[]> {
  return apiFetch<NotificationSummary[]>("/me/notifications");
}

export async function getUnreadCount(): Promise<UnreadCountResponse> {
  return apiFetch<UnreadCountResponse>("/me/notifications/unread-count");
}

export async function markNotificationRead(notificationId: string): Promise<NotificationSummary> {
  return apiFetch<NotificationSummary>(`/me/notifications/${notificationId}/read`, { method: "PATCH" });
}

export async function markAllNotificationsRead(): Promise<void> {
  await apiFetch("/me/notifications/read-all", { method: "POST" });
}

export async function createSupportTicket(data: SupportTicketCreate): Promise<SupportTicketSummary> {
  return apiFetch<SupportTicketSummary>("/me/support-tickets", { method: "POST", body: data });
}

export async function getSupportTickets(): Promise<SupportTicketSummary[]> {
  return apiFetch<SupportTicketSummary[]>("/me/support-tickets");
}

export async function getCmsBlocks(page: string): Promise<CmsBlockSummary[]> {
  return apiFetch<CmsBlockSummary[]>(`/me/cms-blocks?page=${encodeURIComponent(page)}`);
}
