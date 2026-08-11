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
    # Not EmailStr: anonymized ("deleted") users are rewritten to
    # f"deleted-{id}@deleted.bodp.invalid" (see
    # admin_users_service.anonymize_user) — email-validator rejects
    # ".invalid" as a reserved TLD, so a strict EmailStr here 500s
    # ResponseValidationError the moment a deleted user appears in this
    # response (list with status_filter=deleted, or the detail view below).
    # This field only ever reflects already-stored data, never validates
    # new input, so a plain str is correct here.
    email: str
    institution: str | None
    role: str
    status: str
    datasets_granted: int
    deletion_requested_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserDetail(BaseModel):
    id: uuid.UUID
    full_name: str
    # Not EmailStr — see AdminUserSummary.email above.
    email: str
    institution: str | None
    phone: str | None
    role: str
    status: str
    datasets_granted: int
    email_verified_at: datetime | None
    bio: str | None
    research_area: str | None
    deletion_requested_at: datetime | None
    created_at: datetime
    fine_grained_roles: list[str]
    # Only meaningful on the response to POST (account creation) — whether
    # the password-setup email actually sent. None on every other response
    # (GET/PATCH/etc. don't attempt to send anything).
    email_sent: bool | None = None

    model_config = {"from_attributes": True}
