import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CmsBlockCreate(BaseModel):
    """Creates a Custom Block. is_system_block is never accepted here —
    the server always forces it False for admin-created blocks."""

    key: str = Field(min_length=1, max_length=255)
    page: str = Field(min_length=1, max_length=50)
    section: str | None = Field(default=None, max_length=100)
    label: str | None = Field(default=None, max_length=255)
    value: str | None = None
    display_order: int = 0
    is_active: bool = True


class CmsBlockUpdate(BaseModel):
    """key and is_system_block are immutable after create — never accepted
    here, only label/section/value/display_order/is_active can change."""

    section: str | None = Field(default=None, max_length=100)
    label: str | None = Field(default=None, max_length=255)
    value: str | None = None
    display_order: int | None = None
    is_active: bool | None = None


class CmsBlockPublic(BaseModel):
    id: uuid.UUID
    key: str
    page: str
    section: str | None
    label: str | None
    value: str | None
    display_order: int
    is_system_block: bool
    is_active: bool
    updated_at: datetime

    model_config = {"from_attributes": True}
