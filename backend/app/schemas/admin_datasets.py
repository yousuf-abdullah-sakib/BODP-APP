import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.datasets import DatasetFilePublic


class DatasetCreateMinimal(BaseModel):
    """Minimal dataset-shell creation, sufficient to attach files to for
    Phase 2 ingestion testing. The full DatasetModal-equivalent create/edit
    form (category assignment, platforms, processing levels, publish status,
    etc.) is Phase 8 scope — this endpoint intentionally does not attempt to
    replicate that yet."""

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    code: str | None = Field(default=None, max_length=50)


class DatasetMinimalPublic(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str | None
    status: str

    model_config = {"from_attributes": True}


class DatasetCreate(BaseModel):
    """Full admin create/edit form payload. All fields beyond title/
    description are optional so this schema also validates the minimal
    Phase 2 payload shape (`{title, description}` or `{title, description,
    code}`) on the same `POST /admin/datasets` route."""

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    code: str | None = Field(default=None, max_length=50)
    category_id: uuid.UUID | None = None
    location: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default=None, max_length=255)
    platforms: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)
    resolution: str | None = Field(default=None, max_length=100)
    license: str | None = Field(default=None, max_length=255)
    processing_levels: list[str] = Field(default_factory=list)
    status: Literal["draft", "published", "archived"] = "draft"


class DatasetUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    category_id: uuid.UUID | None = None
    location: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default=None, max_length=255)
    platforms: list[str] | None = None
    parameters: list[str] | None = None
    resolution: str | None = Field(default=None, max_length=100)
    license: str | None = Field(default=None, max_length=255)
    processing_levels: list[str] | None = None


class DatasetAdminSummary(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    category_name: str | None
    location: str | None
    record_count: int
    status: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class DatasetAdminDetail(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str | None
    category_id: uuid.UUID | None
    category_name: str | None
    location: str | None
    source: str | None
    platforms: list[str]
    parameters: list[str]
    resolution: str | None
    license: str | None
    processing_levels: list[str]
    formats: list[str]
    status: str
    temporal_start: date | None
    temporal_end: date | None
    record_count: int
    created_at: datetime
    updated_at: datetime
    files: list[DatasetFilePublic]
    active_grant_count: int

    model_config = {"from_attributes": True}


class DatasetPermanentDeleteConfirm(BaseModel):
    confirm: Literal[True]
