from datetime import datetime

from pydantic import BaseModel


class DatabaseHealth(BaseModel):
    healthy: bool
    pool_checked_out: int | None
    pool_size: int | None
    error: str | None = None


class RedisHealth(BaseModel):
    healthy: bool
    used_memory_bytes: int | None
    error: str | None = None


class StorageBackendHealth(BaseModel):
    name: str
    healthy: bool
    error: str | None = None


class CeleryQueueHealth(BaseModel):
    queue: str
    healthy: bool
    worker_count: int


class CeleryHealth(BaseModel):
    healthy: bool
    queues: list[CeleryQueueHealth]
    error: str | None = None


class DiskHealth(BaseModel):
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float


class LastBackupSummary(BaseModel):
    id: str
    status: str
    started_at: datetime
    completed_at: datetime | None


class DetailedHealthResponse(BaseModel):
    checked_at: datetime
    database: DatabaseHealth
    redis: RedisHealth
    storage: list[StorageBackendHealth]
    celery: CeleryHealth
    disk: DiskHealth
    last_backup: LastBackupSummary | None
