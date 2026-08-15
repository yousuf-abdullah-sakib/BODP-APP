import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.catalog import VariableRole

_VALID_ROLES = {r.value for r in VariableRole}


class DatasetVariablePublic(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    name: str
    data_type: str
    unit: str | None
    is_dimension: bool
    roles: list[str]
    min_value: float | None
    max_value: float | None
    distinct_values: list[str] | None
    detected_at: datetime

    model_config = {"from_attributes": True}


class DatasetSchemaReviewSummary(BaseModel):
    """One row in the admin review queue — a dataset plus how much of its
    detected schema still needs a role assignment."""

    dataset_id: uuid.UUID
    dataset_code: str
    dataset_title: str
    variable_count: int
    unassigned_count: int
    schema_reviewed_at: datetime | None
    schema_reviewed_by_name: str | None

    model_config = {"from_attributes": True}


class DatasetSchemaDetail(BaseModel):
    dataset_id: uuid.UUID
    dataset_code: str
    dataset_title: str
    schema_reviewed_at: datetime | None
    schema_reviewed_by_name: str | None
    variables: list[DatasetVariablePublic]

    model_config = {"from_attributes": True}


class DatasetSchemaReviewResult(BaseModel):
    dataset_id: uuid.UUID
    schema_reviewed_at: datetime
    schema_reviewed_by_name: str | None


class VariableRoleUpdate(BaseModel):
    roles: list[str]

    @field_validator("roles")
    @classmethod
    def _roles_are_known(cls, value: list[str]) -> list[str]:
        unknown = set(value) - _VALID_ROLES
        if unknown:
            raise ValueError(f"Unknown role(s): {', '.join(sorted(unknown))}")
        # De-duplicate while preserving first-seen order — a variable
        # holding the same role twice is meaningless, not an error worth
        # rejecting the request over.
        seen: list[str] = []
        for role in value:
            if role not in seen:
                seen.append(role)
        return seen
