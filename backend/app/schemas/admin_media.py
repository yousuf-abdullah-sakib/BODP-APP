import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MediaFileUpdate(BaseModel):
    alt_text: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)


class MediaFilePublic(BaseModel):
    id: uuid.UUID
    file_name: str
    mime_type: str | None
    size_bytes: int | None
    alt_text: str | None
    title: str | None
    url: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}
