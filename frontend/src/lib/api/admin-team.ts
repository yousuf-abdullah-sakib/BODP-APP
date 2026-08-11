import { apiFetch } from "./client";
import type { AdminInviteCreate, AdminTeamMemberPublic } from "@/lib/types/admin-team";

export async function getAdminTeam(): Promise<AdminTeamMemberPublic[]> {
  return apiFetch<AdminTeamMemberPublic[]>("/admin/team");
}

export async function inviteAdmin(payload: AdminInviteCreate): Promise<AdminTeamMemberPublic> {
  return apiFetch<AdminTeamMemberPublic>("/admin/team", { method: "POST", body: payload });
}

export async function resendAdminInvite(userId: string): Promise<{ email_sent: boolean }> {
  return apiFetch<{ email_sent: boolean }>(`/admin/team/${userId}/resend-invite`, { method: "POST" });
}

export async function suspendAdmin(userId: string): Promise<AdminTeamMemberPublic> {
  return apiFetch<AdminTeamMemberPublic>(`/admin/team/${userId}/suspend`, { method: "POST" });
}

export async function reactivateAdmin(userId: string): Promise<AdminTeamMemberPublic> {
  return apiFetch<AdminTeamMemberPublic>(`/admin/team/${userId}/reactivate`, { method: "POST" });
}

// Permanent — anonymizes the account. Distinct from suspendAdmin, which is
// reversible via reactivateAdmin.
export async function removeAdmin(userId: string): Promise<void> {
  await apiFetch(`/admin/team/${userId}`, { method: "DELETE" });
}
