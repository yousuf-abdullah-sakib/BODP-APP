// Mirrors backend/app/schemas/admin_users.py exactly — keep these two in sync.

export interface AdminUserCreate {
  full_name: string;
  email: string;
  institution?: string | null;
  phone?: string | null;
}

export interface AdminUserUpdate {
  full_name?: string;
  institution?: string | null;
  phone?: string | null;
}

export interface AdminUserSummary {
  id: string;
  full_name: string;
  email: string;
  institution: string | null;
  role: string;
  status: string;
  datasets_granted: number;
  deletion_requested_at: string | null;
  created_at: string;
}

export interface AdminUserDetail {
  id: string;
  full_name: string;
  email: string;
  institution: string | null;
  phone: string | null;
  role: string;
  status: string;
  datasets_granted: number;
  email_verified_at: string | null;
  bio: string | null;
  research_area: string | null;
  deletion_requested_at: string | null;
  created_at: string;
  fine_grained_roles: string[];
  // Only set on the response to creating a new user — whether the
  // password-setup email actually sent. null on every other response.
  email_sent: boolean | null;
}
