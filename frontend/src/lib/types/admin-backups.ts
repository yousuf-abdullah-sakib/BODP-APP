// Mirrors backend/app/schemas/admin_backups.py exactly — keep these two in sync.

export interface BackupPublic {
  id: string;
  started_at: string;
  completed_at: string | null;
  size_bytes: number | null;
  status: string;
  celery_task_id: string | null;
  error_message: string | null;
  ready: boolean;
}

export interface BackupDownloadResponse {
  download_url: string;
}
