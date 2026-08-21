import uuid
from datetime import datetime

from pydantic import BaseModel


class BackupPublic(BaseModel):
    id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None
    size_bytes: int | None
    status: str
    celery_task_id: str | None
    error_message: str | None
    ready: bool

    model_config = {"from_attributes": True}


class BackupDownloadResponse(BaseModel):
    download_url: str
