import uuid

from pydantic import BaseModel, Field, field_validator

from app.models.user import PERMISSION_LIST


def _validate_permissions(permissions: list[str]) -> list[str]:
    invalid = [p for p in permissions if p not in PERMISSION_LIST]
    if invalid:
        raise ValueError(f"Unknown permission(s): {', '.join(invalid)}")
    return permissions


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)

    @field_validator("permissions")
    @classmethod
    def _check_permissions(cls, v: list[str]) -> list[str]:
        return _validate_permissions(v)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    permissions: list[str] | None = None

    @field_validator("permissions")
    @classmethod
    def _check_permissions(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return _validate_permissions(v)


class RolePublic(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    permissions: list[str]
    user_count: int

    model_config = {"from_attributes": True}
