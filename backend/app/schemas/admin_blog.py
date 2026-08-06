import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class BlogPostCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    tag_key: str | None = Field(default=None, max_length=50)
    author_name: str | None = Field(default=None, max_length=255)
    excerpt: str | None = None
    content_html: str | None = None
    status: Literal["draft", "published"] = "draft"
    featured: bool = False
    featured_image_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)


class BlogPostUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    tag_key: str | None = Field(default=None, max_length=50)
    author_name: str | None = Field(default=None, max_length=255)
    excerpt: str | None = None
    content_html: str | None = None
    featured: bool | None = None
    featured_image_id: uuid.UUID | None = None
    tags: list[str] | None = None


class BlogPostAdminSummary(BaseModel):
    id: uuid.UUID
    title: str
    category: str | None
    author_name: str | None
    status: str
    featured: bool
    views: int
    featured_image_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BlogPostAdminDetail(BaseModel):
    id: uuid.UUID
    title: str
    category: str | None
    tag_key: str | None
    author_name: str | None
    excerpt: str | None
    content_html: str | None
    status: str
    featured: bool
    featured_image_id: uuid.UUID | None
    featured_image_url: str | None
    tags: list[str]
    views: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
