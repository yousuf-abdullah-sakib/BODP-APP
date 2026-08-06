import { apiFetch } from "./client";
import type {
  AboutTeamMemberCreate,
  AboutTeamMemberPublic,
  AboutTeamMemberUpdate,
} from "@/lib/types/admin-about-team";

export async function getAboutTeamMembers(): Promise<AboutTeamMemberPublic[]> {
  return apiFetch<AboutTeamMemberPublic[]>("/admin/about-team");
}

export async function createAboutTeamMember(
  payload: AboutTeamMemberCreate
): Promise<AboutTeamMemberPublic> {
  return apiFetch<AboutTeamMemberPublic>("/admin/about-team", { method: "POST", body: payload });
}

export async function updateAboutTeamMember(
  id: string,
  payload: AboutTeamMemberUpdate
): Promise<AboutTeamMemberPublic> {
  return apiFetch<AboutTeamMemberPublic>(`/admin/about-team/${id}`, { method: "PATCH", body: payload });
}

export async function deleteAboutTeamMember(id: string): Promise<void> {
  await apiFetch(`/admin/about-team/${id}`, { method: "DELETE" });
}
