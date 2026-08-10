import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class AdminInviteCreate(BaseModel):
    """Invites a brand-new admin account with a specific Role from the
    start (matches Roles & Permissions — no free-text role field)."""

    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    role_id: uuid.UUID


class AdminTeamMemberPublic(BaseModel):
    """An admin-panel account, derived from User + its assigned Role(s) —
    not a separate roster row. `id` is the User's own id."""

    id: uuid.UUID
    full_name: str
    email: str
    status: str
    roles: list[str]
    last_active_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
