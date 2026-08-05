import uuid

from pydantic import BaseModel, Field


class DatasetCreateMinimal(BaseModel):
    """Minimal dataset-shell creation, sufficient to attach files to for
    Phase 2 ingestion testing. The full DatasetModal-equivalent create/edit
    form (category assignment, platforms, processing levels, publish status,
    etc.) is Phase 8 scope — this endpoint intentionally does not attempt to
    replicate that yet."""

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    code: str | None = Field(default=None, max_length=50)


class DatasetMinimalPublic(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    description: str | None
    status: str

    model_config = {"from_attributes": True}
