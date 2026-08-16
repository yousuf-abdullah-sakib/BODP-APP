import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class GrantDuration(StrEnum):
    FIVE_DAYS = "5d"
    TEN_DAYS = "10d"
    ONE_MONTH = "1m"
    TWO_MONTHS = "2m"
    SIX_MONTHS = "6m"
    ONE_YEAR = "1y"
    CUSTOM = "custom"


class SpatialBoundsSchema(BaseModel):
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


class SearchCriteriaSchema(BaseModel):
    """Mirrors the frontend's RequestSearchCriteria — the user's active
    catalog-detail filter state, captured at request time and editable by an
    admin before approval (Master Plan §3 Phase 4 tasks 1 and 4).

    parameters is a list, not a single value — zero selected means "all
    approved parameters" (no filtering), matching the checkbox multi-select
    UI on the dataset detail page. Also used, unchanged in shape, for
    AccessGrant.scope and SubsetExtraction.requested_scope."""

    category: str | None = None
    parameters: list[str] | None = None
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    bounds: SpatialBoundsSchema | None = None


class RequestCreate(BaseModel):
    justification: str
    search_criteria: SearchCriteriaSchema | None = None

    @field_validator("justification")
    @classmethod
    def _justification_min_length(cls, v: str) -> str:
        if len(v.strip()) < 50:
            raise ValueError("Justification must be at least 50 characters.")
        return v


class RequestDatasetSummary(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    category: str | None

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _flatten_category(cls, data):
        # When built from the Dataset ORM object, `.category` is a
        # DatasetCategory relationship, not a string — pull its name, same
        # as catalog.py's _dataset_to_summary()/DatasetDetail construction.
        category = getattr(data, "category", None)
        if category is not None and not isinstance(category, str):
            return {
                "id": data.id,
                "code": data.code,
                "title": data.title,
                "category": category.name,
            }
        return data


class RequestUserSummary(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    institution: str | None

    model_config = {"from_attributes": True}


class RequestSummary(BaseModel):
    """A single request as seen by its own submitter (GET /me/requests)."""

    id: uuid.UUID
    dataset: RequestDatasetSummary
    justification: str
    search_criteria: SearchCriteriaSchema | None
    status: str
    submitted_at: datetime
    reviewed_at: datetime | None
    admin_note: str | None

    model_config = {"from_attributes": True}


class RequestDetail(RequestSummary):
    """Adds requester identity plus dataset-coverage figures for the
    admin review queue — how much of the dataset the requester's own
    search_criteria actually matches, computed once per list call so an
    admin can judge scope at a glance without opening anything."""

    user: RequestUserSummary
    # Defaults let RequestDetail.model_validate() build from a bare ORM
    # object (which has no such attributes); routers always overwrite
    # these via model_copy(update=coverage) with real computed values.
    matching_record_count: int = 0
    dataset_total_record_count: int = 0
    matching_percent: float = 0.0


class ApproveRequestBody(BaseModel):
    search_criteria: SearchCriteriaSchema | None = None
    note: str | None = None
    duration: GrantDuration = GrantDuration.ONE_YEAR
    custom_expires_at: date | None = None

    @model_validator(mode="after")
    def _custom_requires_date(self) -> "ApproveRequestBody":
        # field_validator would only fire when custom_expires_at is
        # explicitly passed — it's None by default, so the "missing" case
        # (duration=custom, field omitted entirely) needs a model-level check.
        if self.duration == GrantDuration.CUSTOM and self.custom_expires_at is None:
            raise ValueError("custom_expires_at is required when duration is 'custom'.")
        return self


class RejectRequestBody(BaseModel):
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("A rejection reason is required.")
        return v


class ExtendGrantBody(BaseModel):
    duration: GrantDuration = GrantDuration.ONE_YEAR
    custom_expires_at: date | None = None

    @model_validator(mode="after")
    def _custom_requires_date(self) -> "ExtendGrantBody":
        if self.duration == GrantDuration.CUSTOM and self.custom_expires_at is None:
            raise ValueError("custom_expires_at is required when duration is 'custom'.")
        return self


class GrantSummary(BaseModel):
    id: uuid.UUID
    dataset: RequestDatasetSummary
    granted_at: datetime
    expires_at: datetime
    status: str
    scope: SearchCriteriaSchema | None

    model_config = {"from_attributes": True}


class GrantDetail(GrantSummary):
    """Adds grantee identity — used on the admin grants table."""

    user: RequestUserSummary
    granted_by_name: str | None


class ExtractionFormat(StrEnum):
    CSV = "csv"
    PARQUET = "parquet"
    NETCDF = "netcdf"
    MAT = "mat"


class ExtractionCreate(BaseModel):
    scope: SearchCriteriaSchema = Field(default_factory=SearchCriteriaSchema)
    format: ExtractionFormat = ExtractionFormat.CSV


class ExtractionStatusResponse(BaseModel):
    id: uuid.UUID
    grant_id: uuid.UUID
    status: str
    format: str | None
    requested_scope: SearchCriteriaSchema | None
    output_size_bytes: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    celery_task_id: str | None

    model_config = {"from_attributes": True}
