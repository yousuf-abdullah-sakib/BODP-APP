import { apiFetch } from "./client";
import type { CmsBlockCreate, CmsBlockPublic, CmsBlockUpdate } from "@/lib/types/admin-cms";

export async function getCmsBlocks(page?: string): Promise<CmsBlockPublic[]> {
  const suffix = page ? `?page=${encodeURIComponent(page)}` : "";
  return apiFetch<CmsBlockPublic[]>(`/admin/cms/blocks${suffix}`);
}

export async function createCmsBlock(payload: CmsBlockCreate): Promise<CmsBlockPublic> {
  return apiFetch<CmsBlockPublic>("/admin/cms/blocks", { method: "POST", body: payload });
}

export async function updateCmsBlock(id: string, payload: CmsBlockUpdate): Promise<CmsBlockPublic> {
  return apiFetch<CmsBlockPublic>(`/admin/cms/blocks/${id}`, { method: "PATCH", body: payload });
}

export async function deleteCmsBlock(id: string): Promise<void> {
  await apiFetch(`/admin/cms/blocks/${id}`, { method: "DELETE" });
}
