import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SupportTicketReplyCreate(BaseModel):
    reply_message: str = Field(min_length=1)


class SupportTicketAdminSummary(BaseModel):
    id: uuid.UUID
    subject: str
    category: str | None
    priority: str
    status: str
    requester_name: str
    requester_email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SupportTicketAdminDetail(BaseModel):
    id: uuid.UUID
    subject: str
    category: str | None
    priority: str
    status: str
    message: str
    requester_name: str
    requester_email: str
    reply_message: str | None
    replied_by_name: str | None
    replied_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
