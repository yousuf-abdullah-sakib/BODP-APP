import uuid
from datetime import date
from enum import StrEnum

from pydantic import BaseModel


class DatasetSort(StrEnum):
    RELEVANCE = "relevance"
    TITLE = "title"
    UPDATED = "updated"
    RECORDS = "records"


class DatasetSummary(BaseModel):
    """Catalog list card — mirrors the prototype's DatasetCard props exactly
    (Master Plan §3 Phase 3 task 1)."""

    id: uuid.UUID
    code: str
    title: str
    category: str | None
    location: str | None
    parameters: list[str]
    source: str | None
    platforms: list[str]
    resolution: str | None
    record_count: int
    formats: list[str]
    license: str | None
    description: str | None
    status: str
    updated_at: str

    model_config = {"from_attributes": True}


class CatalogSearchResponse(BaseModel):
    results: list[DatasetSummary]
    total: int


class SpatialBBox(BaseModel):
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


class DatasetDetail(BaseModel):
    """Full dataset detail — matches the prototype's Dataset type plus
    processing_levels, used by the /catalog/[id] page hero + About panel."""

    id: uuid.UUID
    code: str
    title: str
    category: str | None
    location: str | None
    parameters: list[str]
    source: str | None
    platforms: list[str]
    resolution: str | None
    record_count: int
    formats: list[str]
    processing_levels: list[str]
    license: str | None
    description: str | None
    status: str
    updated_at: str
    temporal_start: date | None
    temporal_end: date | None
    spatial_bbox: SpatialBBox | None

    model_config = {"from_attributes": True}


class StationOption(BaseModel):
    code: str
    name: str
    lat: float
    lon: float


class DatasetRecordPreview(BaseModel):
    """A single previewed row — matches the prototype's Data Preview table
    columns exactly (Date/Station/Depth/Parameter/Value/Unit/Platform/Format/
    Level/Status)."""

    id: uuid.UUID
    time: date
    location: str | None
    depth_m: float | None
    parameter: str
    value: float
    unit: str | None
    platform: str | None
    format: str | None
    processing_level: str | None
    quality_flag: str

    model_config = {"from_attributes": True}


class QualityBreakdown(BaseModel):
    normal: int
    caution: int
    alert: int


class DatasetRecordsResponse(BaseModel):
    """Backs the summary strip (Matching Records / Normal / Caution / Alert /
    % of Dataset) + capped Data Preview table (Master Plan §3 Phase 3 task 3)."""

    preview: list[DatasetRecordPreview]
    matching_count: int
    dataset_total_count: int
    quality_breakdown: QualityBreakdown


class TaxonomyOptions(BaseModel):
    """Populates the catalog filter dropdowns dynamically, replacing the
    prototype's hardcoded taxonomy (Master Plan §3 Phase 3 task 4)."""

    categories: list[str]
    parameters: list[str]
    sources: list[str]
    platforms: list[str]
    formats: list[str]
