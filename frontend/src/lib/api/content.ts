import { apiFetch } from "./client";
import type {
  ContactSubmissionCreate,
  PublicBlogPostDetail,
  PublicBlogPostSummary,
  PublicCmsBlock,
  PublicTeamMember,
} from "@/lib/types/content";

/** Public, unauthenticated read — powers Home/About/Contact/Footer/legal pages. */
export async function getCmsBlocks(page: string): Promise<PublicCmsBlock[]> {
  return apiFetch<PublicCmsBlock[]>(`/content/cms-blocks/${encodeURIComponent(page)}`, {
    skipAuth: true,
  });
}

export async function getTeamMembers(): Promise<PublicTeamMember[]> {
  return apiFetch<PublicTeamMember[]>("/content/about-team", { skipAuth: true });
}

export async function getBlogPosts(params?: {
  category?: string;
  tag_key?: string;
}): Promise<PublicBlogPostSummary[]> {
  const qs = new URLSearchParams();
  if (params?.category) qs.set("category", params.category);
  if (params?.tag_key) qs.set("tag_key", params.tag_key);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<PublicBlogPostSummary[]>(`/content/blog${suffix}`, { skipAuth: true });
}

export async function getBlogPost(id: string): Promise<PublicBlogPostDetail> {
  return apiFetch<PublicBlogPostDetail>(`/content/blog/${id}`, { skipAuth: true });
}

export async function submitContactForm(
  data: ContactSubmissionCreate
): Promise<{ message: string }> {
  return apiFetch<{ message: string }>("/content/contact", {
    method: "POST",
    body: data,
    skipAuth: true,
  });
}

/** Reduces a block list to a `key -> value` lookup, defaulting null values to "". */
export function blocksToMap(blocks: PublicCmsBlock[]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const b of blocks) {
    map[b.key] = b.value ?? "";
  }
  return map;
}
