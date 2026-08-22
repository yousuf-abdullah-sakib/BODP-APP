// Mirrors backend/app/schemas/admin_health.py exactly — keep these two in sync.

export interface DatabaseHealth {
  healthy: boolean;
  pool_checked_out: number | null;
  pool_size: number | null;
  error: string | null;
}

export interface RedisHealth {
  healthy: boolean;
  used_memory_bytes: number | null;
  error: string | null;
}

export interface StorageBackendHealth {
  name: string;
  healthy: boolean;
  error: string | null;
}

export interface CeleryQueueHealth {
  queue: string;
  healthy: boolean;
  worker_count: number;
}

export interface CeleryHealth {
  healthy: boolean;
  queues: CeleryQueueHealth[];
  error: string | null;
}

export interface DiskHealth {
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  percent_used: number;
}

export interface LastBackupSummary {
  id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
}

export interface DetailedHealthResponse {
  checked_at: string;
  database: DatabaseHealth;
  redis: RedisHealth;
  storage: StorageBackendHealth[];
  celery: CeleryHealth;
  disk: DiskHealth;
  last_backup: LastBackupSummary | null;
}
