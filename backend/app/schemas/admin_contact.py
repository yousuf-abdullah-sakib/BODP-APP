import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class ContactSubmissionCreate(BaseModel):
    """Public /contact page form payload — matches ContactForm.tsx's fields."""

    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    organization: str | None = Field(default=None, max_length=200)
    subject: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=20, max_length=2000)


class ContactReplyCreate(BaseModel):
    reply_message: str = Field(min_length=1)


class ContactSubmissionAdminSummary(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    organization: str | None
    subject: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ContactSubmissionAdminDetail(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    organization: str | None
    subject: str
    message: str
    status: str
    reply_message: str | None
    replied_by_name: str | None
    replied_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
