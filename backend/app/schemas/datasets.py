import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


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
    # Multipart/progress fields — null for the existing single-request
    # small-file path, which never populates them.
    total_size_bytes: int | None = None
    total_parts: int | None = None
    uploaded_bytes: int = 0
    progress_stage: str | None = None
    progress_pct: int | None = None

    model_config = {"from_attributes": True}


class DatasetFileUploadResponse(BaseModel):
    upload: UploadStatusResponse
    dataset_file: DatasetFilePublic


class MultipartUploadInitiateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=500)
    total_size_bytes: int = Field(gt=0)


class MultipartUploadInitiateResponse(BaseModel):
    upload: UploadStatusResponse
    total_parts: int
    part_size_bytes: int


class MultipartUploadPresignPartResponse(BaseModel):
    part_number: int
    upload_url: str


class MultipartUploadPartCompleteRequest(BaseModel):
    part_number: int = Field(ge=1)
    size_bytes: int = Field(gt=0)


class MultipartUploadCompleteResponse(BaseModel):
    upload: UploadStatusResponse
    dataset_file: DatasetFilePublic
