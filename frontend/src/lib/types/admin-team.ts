// Mirrors backend/app/schemas/admin_team.py exactly — keep these two in sync.

export interface AdminTeamMemberCreate {
  name: string;
  email: string;
  role_label?: string | null;
}

export interface AdminTeamMemberUpdate {
  name?: string;
  role_label?: string | null;
  status?: string | null;
}

export interface AdminTeamMemberPublic {
  id: string;
  user_id: string | null;
  name: string;
  email: string;
  role_label: string | null;
  status: string;
  last_active_at: string | null;
}
