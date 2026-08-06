import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ReportType = Literal["usage_summary", "dataset_inventory", "user_activity", "access_grants"]


class ReportCreate(BaseModel):
    type: ReportType
    date_from: str | None = None
    date_to: str | None = None


class ReportPublic(BaseModel):
    id: uuid.UUID
    type: str
    date_range: str | None
    generated_by: uuid.UUID | None
    generated_at: datetime
    celery_task_id: str | None
    ready: bool

    model_config = {"from_attributes": True}


class ReportDownloadResponse(BaseModel):
    download_url: str
