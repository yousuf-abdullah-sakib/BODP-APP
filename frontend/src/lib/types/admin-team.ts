// Mirrors backend/app/schemas/admin_team.py exactly — keep these two in sync.

export interface AdminInviteCreate {
  full_name: string;
  email: string;
  role_id: string;
}

export interface AdminTeamMemberPublic {
  id: string;
  full_name: string;
  email: string;
  status: string;
  roles: string[];
  last_active_at: string | null;
  created_at: string;
}
