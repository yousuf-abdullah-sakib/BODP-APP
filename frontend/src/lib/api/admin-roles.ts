import { apiFetch } from "./client";
import type { RoleCreate, RolePublic, RoleUpdate } from "@/lib/types/admin-roles";

export async function getRoles(): Promise<RolePublic[]> {
  return apiFetch<RolePublic[]>("/admin/roles");
}

export async function createRole(payload: RoleCreate): Promise<RolePublic> {
  return apiFetch<RolePublic>("/admin/roles", { method: "POST", body: payload });
}

export async function updateRole(id: string, payload: RoleUpdate): Promise<RolePublic> {
  return apiFetch<RolePublic>(`/admin/roles/${id}`, { method: "PATCH", body: payload });
}

export async function deleteRole(id: string): Promise<void> {
  await apiFetch(`/admin/roles/${id}`, { method: "DELETE" });
}
