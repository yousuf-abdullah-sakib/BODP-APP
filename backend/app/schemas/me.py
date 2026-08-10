import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# --- Overview (task 1) ---


class ActivityItem(BaseModel):
    """One entry in the real, merged-and-sorted recent-activity feed —
    synthesized from DatasetRequest/DownloadLog/AuditLogEntry, not backed
    by a dedicated table."""

    type: str  # "request_submitted" | "request_approved" | "request_rejected" | "download" | "grant_extended" | "grant_revoked"
    description: str
    occurred_at: datetime


class OverviewResponse(BaseModel):
    pending_requests: int
    approved_requests: int
    datasets_granted: int
    total_downloads: int
    datasets_viewed: int
    total_extracted_bytes: int
    member_since: datetime
    recent_activity: list[ActivityItem]


# --- Profile (task 2) ---


class ProfileDetail(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: str
    institution: str | None = None
    phone: str | None = None
    bio: str | None = None
    research_area: str | None = None
    avatar_key: str | None = None
    datasets_granted: int
    created_at: datetime
    deletion_requested_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    institution: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    bio: str | None = None
    research_area: str | None = Field(default=None, max_length=255)


class AvatarUploadResponse(BaseModel):
    avatar_key: str


# --- Security / sessions (task 3) ---


class SessionSummary(BaseModel):
    id: uuid.UUID
    device: str | None
    ip_address: str | None
    location: str | None
    user_agent: str | None
    created_at: datetime
    last_active_at: datetime
    is_current: bool

    model_config = {"from_attributes": True}


class DeletionStatusResponse(BaseModel):
    deletion_requested_at: datetime | None
    grace_period_days: int = 30


# --- Preferences (task 4) ---


class PreferencesSchema(BaseModel):
    notify_request_status: bool = True
    notify_new_dataset: bool = True
    notify_weekly_digest: bool = False
    notify_security_alerts: bool = True
    notify_newsletter: bool = False
    date_format: str = "iso"
    coordinate_format: str = "dd"
    compact_table_rows: bool = False

    model_config = {"from_attributes": True}


# --- Notifications (task 5) ---


class NotificationSummary(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    description: str | None
    unread: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountResponse(BaseModel):
    unread_count: int


# --- Support tickets (task 6) ---


class SupportTicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    priority: str = "medium"
    message: str = Field(min_length=1)


class SupportTicketSummary(BaseModel):
    id: uuid.UUID
    subject: str
    category: str | None
    priority: str
    status: str
    message: str
    reply_message: str | None
    replied_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- CMS content (task 7) ---


class CmsBlockSummary(BaseModel):
    key: str
    page: str
    label: str | None
    value: str | None

    model_config = {"from_attributes": True}
