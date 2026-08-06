import uuid

from pydantic import BaseModel, Field


class AboutTeamMemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    bio: str | None = None
    photo_id: uuid.UUID | None = None
    display_order: int = 0


class AboutTeamMemberUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    bio: str | None = None
    photo_id: uuid.UUID | None = None
    display_order: int | None = None


class AboutTeamMemberPublic(BaseModel):
    id: uuid.UUID
    name: str
    role: str | None
    bio: str | None
    photo_id: uuid.UUID | None
    photo_url: str | None
    display_order: int

    model_config = {"from_attributes": True}
