import { apiFetch } from "./client";
import type { MediaFilePublic, MediaFileUpdate } from "@/lib/types/admin-media";

export async function getMediaFiles(params?: {
  search?: string;
  mime_prefix?: string;
}): Promise<MediaFilePublic[]> {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.mime_prefix) qs.set("mime_prefix", params.mime_prefix);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<MediaFilePublic[]>(`/admin/media${suffix}`);
}

export async function uploadMediaFile(
  file: File,
  opts?: { alt_text?: string; title?: string }
): Promise<MediaFilePublic> {
  const qs = new URLSearchParams();
  if (opts?.alt_text) qs.set("alt_text", opts.alt_text);
  if (opts?.title) qs.set("title", opts.title);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";

  const form = new FormData();
  form.set("file", file);
  return apiFetch<MediaFilePublic>(`/admin/media${suffix}`, { method: "POST", body: form });
}

export async function updateMediaFile(id: string, payload: MediaFileUpdate): Promise<MediaFilePublic> {
  return apiFetch<MediaFilePublic>(`/admin/media/${id}`, { method: "PATCH", body: payload });
}

export async function deleteMediaFile(id: string): Promise<void> {
  await apiFetch(`/admin/media/${id}`, { method: "DELETE" });
}
