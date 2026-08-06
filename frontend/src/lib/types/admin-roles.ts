// Mirrors backend/app/schemas/admin_roles.py exactly — keep these two in sync.

export interface RoleCreate {
  name: string;
  description?: string | null;
  permissions?: string[];
}

export interface RoleUpdate {
  name?: string;
  description?: string | null;
  permissions?: string[] | null;
}

export interface RolePublic {
  id: string;
  name: string;
  description: string | null;
  permissions: string[];
  user_count: number;
}

// Literal TS copy of backend/app/models/user.py's PERMISSION_LIST — used to
// render the RoleModal checklist. Keep in sync with the backend constant.
export const PERMISSION_LIST = [
  "Approve Requests",
  "Manage Users",
  "Edit Datasets",
  "Delete Datasets",
  "Publish Content",
  "Manage Roles",
  "View Analytics",
  "Manage Backups",
  // Phase 9 — content/reporting admin surfaces.
  "Manage CMS",
  "Manage Blog",
  "Manage Media",
  "View Reports",
  "View Audit Log",
] as const;
