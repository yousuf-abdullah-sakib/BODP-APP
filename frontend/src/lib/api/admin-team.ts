import { apiFetch } from "./client";
import type { AdminInviteCreate, AdminTeamMemberPublic } from "@/lib/types/admin-team";

export async function getAdminTeam(): Promise<AdminTeamMemberPublic[]> {
  return apiFetch<AdminTeamMemberPublic[]>("/admin/team");
}

export async function inviteAdmin(payload: AdminInviteCreate): Promise<AdminTeamMemberPublic> {
  return apiFetch<AdminTeamMemberPublic>("/admin/team", { method: "POST", body: payload });
}

export async function removeAdmin(userId: string): Promise<void> {
  await apiFetch(`/admin/team/${userId}`, { method: "DELETE" });
}
