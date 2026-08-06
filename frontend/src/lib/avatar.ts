const STORAGE_PUBLIC_URL = process.env.NEXT_PUBLIC_STORAGE_PUBLIC_URL ?? "http://localhost:9000";
const STORAGE_BUCKET = process.env.NEXT_PUBLIC_STORAGE_BUCKET ?? "bodp-vps";

/**
 * Resolves a bare storage object key (e.g. `avatars/{user_id}/{file}.png`,
 * as stored in User.avatar_key) into a fetchable URL — mirrors how the
 * backend builds presigned download URLs, except avatars are served
 * directly off the public bucket rather than presigned per-request.
 */
export function avatarUrl(key: string | null | undefined): string | null {
  if (!key) return null;
  return `${STORAGE_PUBLIC_URL}/${STORAGE_BUCKET}/${key}`;
}
