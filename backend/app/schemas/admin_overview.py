from datetime import datetime

from pydantic import BaseModel


class StatCardValue(BaseModel):
    current: int
    previous: int | None
    delta_pct: float | None
    sparkline: list[int]


class ActivityEntry(BaseModel):
    description: str
    occurred_at: datetime


class RequestPreview(BaseModel):
    id: str
    dataset_title: str
    requester_name: str
    submitted_at: datetime


class StorageUsageSchema(BaseModel):
    used_bytes: int
    capacity_bytes: int | None
    percent_used: float | None


class SystemHealthSchema(BaseModel):
    database: bool
    redis: bool
    storage: bool
    checked_at: datetime


class AdminOverviewResponse(BaseModel):
    total_users: StatCardValue
    active_users: StatCardValue
    total_datasets: StatCardValue
    downloads: StatCardValue
    pending_requests: StatCardValue
    recent_requests: list[RequestPreview]
    recent_activity: list[ActivityEntry]
    category_distribution: dict[str, int]
    storage: StorageUsageSchema
    top_downloaded: list[dict]
    system_health: SystemHealthSchema


class StorageCapacitySchema(BaseModel):
    storage_capacity_bytes: int | None

    model_config = {"from_attributes": True}


class StorageCapacityUpdate(BaseModel):
    storage_capacity_bytes: int | None = None
