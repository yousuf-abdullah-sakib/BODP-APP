import uuid
from datetime import date, datetime

from pydantic import BaseModel


class DatasetFilePublic(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    file_name: str
    storage_backend: str
    file_format: str | None
    file_size_bytes: int | None
    checksum: str | None
    temporal_start: date | None
    temporal_end: date | None
    version: int
    file_metadata: dict | None

    model_config = {"from_attributes": True}


class UploadStatusResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID | None
    dataset_file_id: uuid.UUID | None
    file_name: str
    size_bytes: int | None
    status: str
    error_message: str | None
    uploaded_at: datetime
    celery_task_id: str | None

    model_config = {"from_attributes": True}


class DatasetFileUploadResponse(BaseModel):
    upload: UploadStatusResponse
    dataset_file: DatasetFilePublic
