import uuid
from datetime import datetime

from pydantic import BaseModel, computed_field


class PublicCmsBlock(BaseModel):
    key: str
    page: str
    section: str | None
    label: str | None
    value: str | None
    display_order: int


class PublicTeamMember(BaseModel):
    id: uuid.UUID
    name: str
    role: str | None
    bio: str | None
    photo_url: str | None
    display_order: int

    model_config = {"from_attributes": True}


_WORDS_PER_MINUTE = 200


def _read_time_minutes(content_html: str | None) -> int:
    if not content_html:
        return 1
    import re

    text = re.sub(r"<[^>]+>", " ", content_html)
    word_count = len(text.split())
    return max(1, -(-word_count // _WORDS_PER_MINUTE))  # ceil division


class PublicBlogPostSummary(BaseModel):
    id: uuid.UUID
    title: str
    category: str | None
    tag_key: str | None
    author_name: str | None
    excerpt: str | None
    featured: bool
    featured_image_url: str | None
    tags: list[str]
    views: int
    created_at: datetime


class PublicBlogPostDetail(PublicBlogPostSummary):
    content_html: str | None

    @computed_field
    @property
    def read_time_minutes(self) -> int:
        return _read_time_minutes(self.content_html)
