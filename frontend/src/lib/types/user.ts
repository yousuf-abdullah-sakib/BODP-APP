// Mirrors backend/app/schemas/auth.py's UserPublic.
export type UserRole = "user" | "admin";

export interface SessionUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  institution?: string | null;
  avatar_key?: string | null;
}
