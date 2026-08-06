import { apiFetch } from "./client";
import type { CategoryCreate, CategoryPublic, CategoryUpdate } from "@/lib/types/admin-categories";

export async function getCategories(): Promise<CategoryPublic[]> {
  return apiFetch<CategoryPublic[]>("/admin/categories");
}

export async function createCategory(payload: CategoryCreate): Promise<CategoryPublic> {
  return apiFetch<CategoryPublic>("/admin/categories", { method: "POST", body: payload });
}

export async function updateCategory(id: string, payload: CategoryUpdate): Promise<CategoryPublic> {
  return apiFetch<CategoryPublic>(`/admin/categories/${id}`, { method: "PATCH", body: payload });
}

export async function deleteCategory(id: string): Promise<void> {
  await apiFetch(`/admin/categories/${id}`, { method: "DELETE" });
}
