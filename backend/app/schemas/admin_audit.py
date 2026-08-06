import uuid
from datetime import datetime

from pydantic import BaseModel


class AuditLogEntryPublic(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_name: str | None
    action: str
    action_type: str
    target: str | None
    ip_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogPage(BaseModel):
    items: list[AuditLogEntryPublic]
    total: int
    page: int
    page_size: int
