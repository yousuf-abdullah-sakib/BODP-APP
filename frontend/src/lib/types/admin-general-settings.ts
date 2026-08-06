// Mirrors backend/app/schemas/admin_general_settings.py exactly — keep in sync.

export interface GeneralSettingsSchema {
  site_name: string;
  contact_email: string | null;
  data_access_email: string | null;
  max_upload_size_mb: number;
  session_lifetime_min: number;
}

export interface GeneralSettingsUpdate {
  site_name?: string;
  contact_email?: string | null;
  data_access_email?: string | null;
  max_upload_size_mb?: number;
  session_lifetime_min?: number;
}

export interface NotificationSettingsSchema {
  notify_new_request: boolean;
  notify_new_user: boolean;
  notify_expiring_dataset: boolean;
}

export interface NotificationSettingsUpdate {
  notify_new_request?: boolean;
  notify_new_user?: boolean;
  notify_expiring_dataset?: boolean;
}

// Mirrors backend/app/schemas/admin_overview.py's storage-capacity pair.
export interface StorageCapacitySchema {
  storage_capacity_bytes: number | null;
}

export interface StorageCapacityUpdate {
  storage_capacity_bytes: number | null;
}
