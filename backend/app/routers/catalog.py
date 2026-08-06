import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user_optional
from app.models.catalog import Dataset, DatasetView
from app.models.user import User
from app.schemas.catalog import (
    CatalogSearchResponse,
    DatasetDetail,
    DatasetRecordsResponse,
    DatasetSort,
    DatasetSummary,
    QualityBreakdown,
    SpatialBBox,
    StationOption,
    TaxonomyOptions,
)
from app.services.catalog_service import (
    RecordsFilter,
    get_filtered_records,
    get_published_dataset,
    get_station_options_for_dataset,
    get_taxonomy_options,
    search_datasets,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])

_PREVIEW_ROW_LIMIT = 6


def _dataset_to_summary(dataset: Dataset) -> DatasetSummary:
    return DatasetSummary(
        id=dataset.id,
        code=dataset.code,
        title=dataset.title,
        category=dataset.category.name if dataset.category else None,
        location=dataset.location,
        parameters=dataset.parameters or [],
        source=dataset.source,
        platforms=dataset.platforms or [],
        resolution=dataset.resolution,
        record_count=dataset.record_count,
        formats=dataset.formats or [],
        license=dataset.license,
        description=dataset.description,
        status=dataset.status,
        updated_at=dataset.updated_at.date().isoformat(),
    )


@router.get("/search", response_model=CatalogSearchResponse)
async def catalog_search(
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    parameter: str | None = Query(default=None),
    source: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    format: str | None = Query(default=None),
    sort: DatasetSort = Query(default=DatasetSort.RELEVANCE),
    db: AsyncSession = Depends(get_db),
):
    """Dataset catalog search — category/parameter/source/platform/format
    filters, relevance-scored free text, sort options (Master Plan §3 Phase 3
    task 1). Only published datasets are ever returned."""
    datasets, total = await search_datasets(
        db,
        search=search,
        category=category,
        parameter=parameter,
        source=source,
        platform=platform,
        format_=format,
        sort=sort,
    )
    return CatalogSearchResponse(results=[_dataset_to_summary(d) for d in datasets], total=total)


@router.get("/taxonomy", response_model=TaxonomyOptions)
async def catalog_taxonomy(db: AsyncSession = Depends(get_db)):
    """Populates catalog filter dropdowns dynamically (Master Plan §3 Phase 3
    task 4), Redis-cached (task 6)."""
    options = await get_taxonomy_options(db)
    return TaxonomyOptions(**options)


@router.get("/{dataset_id}", response_model=DatasetDetail)
async def catalog_dataset_detail(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Dataset detail: meta, available parameters/platforms/processing
    levels (Master Plan §3 Phase 3 task 2). Logged-in views are recorded
    (Master Plan §3 Phase 6 task 1, backs Overview's "datasets viewed"
    stat) — anonymous views aren't logged since there's no user to
    attribute them to."""
    dataset = await get_published_dataset(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    if current_user is not None:
        db.add(DatasetView(user_id=current_user.id, dataset_id=dataset_id))
        await db.commit()

    spatial_bbox = None
    if dataset.spatial_extent is not None:
        result = await db.execute(
            select(
                func.ST_XMin(dataset.spatial_extent),
                func.ST_XMax(dataset.spatial_extent),
                func.ST_YMin(dataset.spatial_extent),
                func.ST_YMax(dataset.spatial_extent),
            )
        )
        row = result.one_or_none()
        if row is not None and None not in row:
            lon_min, lon_max, lat_min, lat_max = row
            spatial_bbox = SpatialBBox(
                lat_min=lat_min, lat_max=lat_max, lon_min=lon_min, lon_max=lon_max
            )

    return DatasetDetail(
        id=dataset.id,
        code=dataset.code,
        title=dataset.title,
        category=dataset.category.name if dataset.category else None,
        location=dataset.location,
        parameters=dataset.parameters or [],
        source=dataset.source,
        platforms=dataset.platforms or [],
        resolution=dataset.resolution,
        record_count=dataset.record_count,
        formats=dataset.formats or [],
        processing_levels=dataset.processing_levels or [],
        license=dataset.license,
        description=dataset.description,
        status=dataset.status,
        updated_at=dataset.updated_at.date().isoformat(),
        temporal_start=dataset.temporal_start,
        temporal_end=dataset.temporal_end,
        spatial_bbox=spatial_bbox,
    )


@router.get("/{dataset_id}/stations", response_model=list[StationOption])
async def catalog_dataset_stations(dataset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    dataset = await get_published_dataset(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    stations = await get_station_options_for_dataset(db, dataset_id)
    return [
        StationOption(code=s.code, name=s.name, lat=float(s.lat), lon=float(s.lon))
        for s in stations
    ]


@router.get("/{dataset_id}/records", response_model=DatasetRecordsResponse)
async def catalog_dataset_records(
    dataset_id: uuid.UUID,
    parameter: str | None = Query(default=None),
    quality: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    lat_min: float | None = Query(default=None),
    lat_max: float | None = Query(default=None),
    lon_min: float | None = Query(default=None),
    lon_max: float | None = Query(default=None),
    depth_min: float | None = Query(default=None),
    depth_max: float | None = Query(default=None),
    source: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    station: str | None = Query(default=None),
    format: str | None = Query(default=None),
    processing_level: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Filtered preview query: bbox via PostGIS ST_Intersects, date range,
    depth range, parameter, quality flag, processing level, station — capped
    preview (6 rows) + full matching-count + per-quality-flag breakdown
    (Master Plan §3 Phase 3 task 3)."""
    dataset = await get_published_dataset(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    record_filter = RecordsFilter(
        parameter=parameter,
        quality=quality,
        date_from=date_from,
        date_to=date_to,
        lat_min=lat_min,
        lat_max=lat_max,
        lon_min=lon_min,
        lon_max=lon_max,
        depth_min=depth_min,
        depth_max=depth_max,
        source=source,
        platform=platform,
        station=station,
        format_=format,
        processing_level=processing_level,
    )

    preview_rows, matching_count, dataset_total_count, breakdown = await get_filtered_records(
        db, dataset_id, record_filter, preview_limit=_PREVIEW_ROW_LIMIT
    )

    return DatasetRecordsResponse(
        preview=[
            {
                "id": r.id,
                "time": r.time,
                "location": r.location,
                "depth_m": float(r.depth_m) if r.depth_m is not None else None,
                "parameter": r.parameter,
                "value": float(r.value),
                "unit": r.unit,
                "platform": r.platform,
                "format": r.format,
                "processing_level": r.processing_level,
                "quality_flag": r.quality_flag,
            }
            for r in preview_rows
        ],
        matching_count=matching_count,
        dataset_total_count=dataset_total_count,
        quality_breakdown=QualityBreakdown(**breakdown),
    )
