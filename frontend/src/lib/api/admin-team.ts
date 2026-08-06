import { apiFetch } from "./client";
import type { AdminTeamMemberCreate, AdminTeamMemberPublic, AdminTeamMemberUpdate } from "@/lib/types/admin-team";

export async function getAdminTeam(): Promise<AdminTeamMemberPublic[]> {
  return apiFetch<AdminTeamMemberPublic[]>("/admin/team");
}

export async function inviteAdmin(payload: AdminTeamMemberCreate): Promise<AdminTeamMemberPublic> {
  return apiFetch<AdminTeamMemberPublic>("/admin/team", { method: "POST", body: payload });
}

export async function updateAdminTeamMember(
  id: string,
  payload: AdminTeamMemberUpdate
): Promise<AdminTeamMemberPublic> {
  return apiFetch<AdminTeamMemberPublic>(`/admin/team/${id}`, { method: "PATCH", body: payload });
}

export async function removeAdminTeamMember(id: string): Promise<void> {
  await apiFetch(`/admin/team/${id}`, { method: "DELETE" });
}
