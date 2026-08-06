import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class AdminUserCreate(BaseModel):
    """Admin-initiated user creation — no password field (the new user sets
    their own via the email invite/set-password flow), no role field
    (always created as role="user"; admins are created via Admin Team)."""

    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    institution: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)


class AdminUserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    institution: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)


class AdminUserSummary(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    institution: str | None
    role: str
    status: str
    datasets_granted: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserDetail(BaseModel):
    id: uuid.UUID
    full_name: str
    email: EmailStr
    institution: str | None
    phone: str | None
    role: str
    status: str
    datasets_granted: int
    email_verified_at: datetime | None
    bio: str | None
    research_area: str | None
    created_at: datetime
    fine_grained_roles: list[str]

    model_config = {"from_attributes": True}
