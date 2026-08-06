import uuid

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    color_tag: str = Field(default="cat-default", max_length=50)


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    color_tag: str | None = Field(default=None, max_length=50)


class CategoryPublic(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    color_tag: str
    dataset_count: int

    model_config = {"from_attributes": True}
