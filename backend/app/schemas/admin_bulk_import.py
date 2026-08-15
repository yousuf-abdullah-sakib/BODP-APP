import uuid

from pydantic import BaseModel, Field

from app.schemas.datasets import UploadStatusResponse


class BulkImportValidateRequest(BaseModel):
    dataset_id: uuid.UUID
    source_path: str = Field(min_length=1)


class BulkImportValidateResponse(BaseModel):
    """Mirrors app.scripts.bulk_import.validate_bulk_import's plan dict —
    a preview only, nothing is written yet."""

    dataset_id: str
    dataset_title: str
    source_path: str
    filename: str
    size_bytes: int
    extension: str
    target_bucket: str
    target_key: str


class BulkImportStartRequest(BaseModel):
    dataset_id: uuid.UUID
    source_path: str = Field(min_length=1)


class BulkImportStartResponse(BaseModel):
    """Returned immediately (the slow transfer itself runs in the
    background) — upload carries a real, already-pollable Upload.id via
    the existing GET /admin/datasets/uploads/{upload_id} endpoint, the
    same one browser uploads already use."""

    upload: UploadStatusResponse
