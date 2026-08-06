import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class AdminTeamMemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    role_label: str | None = Field(default=None, max_length=100)


class AdminTeamMemberUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    role_label: str | None = Field(default=None, max_length=100)
    status: str | None = None


class AdminTeamMemberPublic(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    name: str
    email: str
    role_label: str | None
    status: str
    last_active_at: datetime | None

    model_config = {"from_attributes": True}
