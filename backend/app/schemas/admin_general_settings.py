from pydantic import BaseModel, Field


class GeneralSettingsSchema(BaseModel):
    site_name: str
    contact_email: str | None
    data_access_email: str | None
    max_upload_size_mb: int
    session_lifetime_min: int

    model_config = {"from_attributes": True}


class GeneralSettingsUpdate(BaseModel):
    site_name: str | None = Field(default=None, min_length=1, max_length=255)
    contact_email: str | None = None
    data_access_email: str | None = None
    max_upload_size_mb: int | None = Field(default=None, gt=0)
    session_lifetime_min: int | None = Field(default=None, gt=0)


class NotificationSettingsSchema(BaseModel):
    notify_new_request: bool
    notify_new_user: bool
    notify_expiring_dataset: bool

    model_config = {"from_attributes": True}


class NotificationSettingsUpdate(BaseModel):
    notify_new_request: bool | None = None
    notify_new_user: bool | None = None
    notify_expiring_dataset: bool | None = None
