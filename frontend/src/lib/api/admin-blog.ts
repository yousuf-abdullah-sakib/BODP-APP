import { apiFetch } from "./client";
import type {
  BlogPostAdminDetail,
  BlogPostAdminSummary,
  BlogPostCreate,
  BlogPostUpdate,
} from "@/lib/types/admin-blog";

export async function getAdminBlogPosts(params?: {
  search?: string;
  status_filter?: string;
}): Promise<BlogPostAdminSummary[]> {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.status_filter) qs.set("status_filter", params.status_filter);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<BlogPostAdminSummary[]>(`/admin/blog${suffix}`);
}

export async function getAdminBlogPost(id: string): Promise<BlogPostAdminDetail> {
  return apiFetch<BlogPostAdminDetail>(`/admin/blog/${id}`);
}

export async function createBlogPost(payload: BlogPostCreate): Promise<BlogPostAdminDetail> {
  return apiFetch<BlogPostAdminDetail>("/admin/blog", { method: "POST", body: payload });
}

export async function updateBlogPost(id: string, payload: BlogPostUpdate): Promise<BlogPostAdminDetail> {
  return apiFetch<BlogPostAdminDetail>(`/admin/blog/${id}`, { method: "PATCH", body: payload });
}

export async function publishBlogPost(id: string): Promise<BlogPostAdminDetail> {
  return apiFetch<BlogPostAdminDetail>(`/admin/blog/${id}/publish`, { method: "POST" });
}

export async function unpublishBlogPost(id: string): Promise<BlogPostAdminDetail> {
  return apiFetch<BlogPostAdminDetail>(`/admin/blog/${id}/unpublish`, { method: "POST" });
}

export async function deleteBlogPost(id: string): Promise<void> {
  await apiFetch(`/admin/blog/${id}`, { method: "DELETE" });
}
