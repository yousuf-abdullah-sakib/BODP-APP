// Mirrors backend/app/schemas/admin_about_team.py exactly — keep these two in sync.

export interface AboutTeamMemberCreate {
  name: string;
  role?: string | null;
  bio?: string | null;
  photo_id?: string | null;
  display_order?: number;
}

export interface AboutTeamMemberUpdate {
  name?: string;
  role?: string | null;
  bio?: string | null;
  photo_id?: string | null;
  display_order?: number | null;
}

export interface AboutTeamMemberPublic {
  id: string;
  name: string;
  role: string | null;
  bio: string | null;
  photo_id: string | null;
  photo_url: string | null;
  display_order: number;
}
