import hashlib
import json
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get_json, cache_set_json
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user_optional
from app.core.limiter import limiter
from app.models.catalog import Dataset, DatasetView
from app.models.user import User
from app.schemas.catalog import (
    CatalogSearchResponse,
    DatasetDetail,
    DatasetRecordsResponse,
    DatasetSchemaFilters,
    DatasetSort,
    DatasetSummary,
    QualityBreakdown,
    SpatialBBox,
    StationOption,
    TaxonomyOptions,
)
from app.services.catalog_service import (
    RecordsFilter,
    get_dataset_schema_for_filters,
    get_dataset_snapshot,
    get_dataset_temporal_extent,
    get_filtered_records,
    get_published_dataset,
    get_station_options_for_dataset,
    get_taxonomy_options,
    search_datasets,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])

_PREVIEW_ROW_LIMIT = 6
# Performance & Behavior investigation, Phase 5: this endpoint's matching/
# total counts for a gridded (Zarr) dataset go through the same exact,
# non-metadata .count() computation the Visualize coverage endpoint does
# (gridded_query_service.get_matching_record_counts) — confirmed ~24.8s
# for a real 19.6M-cell dataset with no filter. Caching here mirrors the
# same Redis pattern already used by /visualize/timeseries|comparison|
# statistics|coverage (see visualize_service.py's _cache_key/
# _CACHE_TTL_SECONDS) — same 300s TTL, same "cache the final response
# shape" approach, since preview_rows is a mix of ORM objects and duck-
# typed dataclasses that isn't directly JSON-cacheable, while the already-
# router-shaped DatasetRecordsResponse is.
_RECORDS_CACHE_TTL_SECONDS = 300


def _records_cache_key(dataset_id: uuid.UUID, f: RecordsFilter, preview_limit: int) -> str:
    payload = {
        "dataset_id": str(dataset_id),
        "preview_limit": preview_limit,
        "parameters": sorted(f.parameters) if f.parameters else None,
        "quality": f.quality,
        "date_from": f.date_from.isoformat() if f.date_from else None,
        "date_to": f.date_to.isoformat() if f.date_to else None,
        "lat_min": f.lat_min,
        "lat_max": f.lat_max,
        "lon_min": f.lon_min,
        "lon_max": f.lon_max,
        "depth_min": f.depth_min,
        "depth_max": f.depth_max,
        "source": f.source,
        "platform": f.platform,
        "station": f.station,
        "format_": f.format_,
        "processing_level": f.processing_level,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"catalog:records:{digest}"


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
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def catalog_search(
    request: Request,
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
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def catalog_taxonomy(request: Request, db: AsyncSession = Depends(get_db)):
    """Populates catalog filter dropdowns dynamically (Master Plan §3 Phase 3
    task 4), Redis-cached (task 6)."""
    options = await get_taxonomy_options(db)
    return TaxonomyOptions(**options)


@router.get("/{dataset_id}", response_model=DatasetDetail)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def catalog_dataset_detail(
    request: Request,
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

    # Dataset.temporal_start/temporal_end (the dataset-level fields) are
    # only ever set by the demo-seeding script, never by real ingestion —
    # aggregating DatasetFile.temporal_start/temporal_end (reliably set
    # per-file at ingestion time, for both legacy and Phase 5 files) is
    # the actually-populated source. Cheap (dataset_files row count is
    # small), no data scan.
    temporal_start, temporal_end = await get_dataset_temporal_extent(db, dataset_id)

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
        temporal_start=temporal_start,
        temporal_end=temporal_end,
        spatial_bbox=spatial_bbox,
    )


@router.get("/{dataset_id}/schema", response_model=DatasetSchemaFilters | None)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def catalog_dataset_schema(
    request: Request, dataset_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Admin-approved variable schema for schema-driven filter rendering
    (PLAN.md Phase 4) — null for a dataset not yet reviewed (Phase 3),
    which is the frontend's signal to fall back to today's fixed
    dataset.parameters-driven filters rather than a hard cutover."""
    dataset = await get_published_dataset(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return await get_dataset_schema_for_filters(db, dataset_id)


@router.get("/{dataset_id}/stations", response_model=list[StationOption])
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def catalog_dataset_stations(
    request: Request, dataset_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    dataset = await get_published_dataset(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    stations = await get_station_options_for_dataset(db, dataset_id)
    return [
        StationOption(code=s.code, name=s.name, lat=float(s.lat), lon=float(s.lon))
        for s in stations
    ]


@router.get("/{dataset_id}/records", response_model=DatasetRecordsResponse)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def catalog_dataset_records(
    request: Request,
    dataset_id: uuid.UUID,
    parameters: list[str] | None = Query(default=None),
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
    depth range, parameters (multi-select — zero selected means no
    parameter filtering), quality flag, processing level, station — capped
    preview (6 rows) + full matching-count + per-quality-flag breakdown
    (Master Plan §3 Phase 3 task 3)."""
    dataset = await get_published_dataset(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    record_filter = RecordsFilter(
        parameters=parameters,
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

    # Dataset Default-View Snapshot feature: the Data Page's own initial
    # load is NOT a bare/empty request — useDatasetFilters.ts auto-fills
    # date_from/date_to/lat_min/lat_max/lon_min/lon_max from the dataset's
    # own real extent as soon as it's known (confirmed by reading that
    # hook directly), so a strict "every field is None" check never
    # matches even on first paint. This instead treats a date/bbox range
    # matching the dataset's own real extent — exactly what that auto-fill
    # produces — as equivalent to "no restriction", while parameters/
    # quality/depth/source/platform/station/format/processing_level (a
    # user genuinely cannot have touched these without deliberately
    # picking a value) must still all be unset. This is the exact "just
    # opened this dataset" shape the snapshot exists for, and also,
    # unintuitively, the MOST expensive live-query shape (nothing to
    # prune the scan by) — checked ahead of even the Redis cache below.
    # Any real filter present, or a stale/missing snapshot, falls
    # straight through to the unchanged existing code.
    no_non_extent_filters = not any(
        [parameters, quality, depth_min, depth_max, source, platform, station, format, processing_level]
    )
    if no_non_extent_filters:
        extent_temporal_start, extent_temporal_end = await get_dataset_temporal_extent(db, dataset_id)
        date_matches_extent = (date_from is None or date_from == extent_temporal_start) and (
            date_to is None or date_to == extent_temporal_end
        )

        bbox_matches_extent = True
        if any([lat_min, lat_max, lon_min, lon_max]):
            bbox_matches_extent = False
            if dataset.spatial_extent is not None:
                extent_row = (
                    await db.execute(
                        select(
                            func.ST_XMin(dataset.spatial_extent), func.ST_XMax(dataset.spatial_extent),
                            func.ST_YMin(dataset.spatial_extent), func.ST_YMax(dataset.spatial_extent),
                        )
                    )
                ).one_or_none()
                if extent_row is not None and None not in extent_row:
                    ext_lon_min, ext_lon_max, ext_lat_min, ext_lat_max = extent_row
                    # Frontend sends 3-decimal-rounded strings (toFixed(3))
                    # of the exact same PostGIS-derived values — a tiny
                    # tolerance absorbs that rounding without accidentally
                    # matching a genuinely different, merely-close bbox.
                    bbox_matches_extent = (
                        (lat_min is None or abs(lat_min - float(ext_lat_min)) < 0.001)
                        and (lat_max is None or abs(lat_max - float(ext_lat_max)) < 0.001)
                        and (lon_min is None or abs(lon_min - float(ext_lon_min)) < 0.001)
                        and (lon_max is None or abs(lon_max - float(ext_lon_max)) < 0.001)
                    )

        if date_matches_extent and bbox_matches_extent:
            snapshot = await get_dataset_snapshot(dataset)
            if snapshot is not None:
                return DatasetRecordsResponse.model_validate(snapshot["records"])

    cache_key = _records_cache_key(dataset_id, record_filter, _PREVIEW_ROW_LIMIT)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return DatasetRecordsResponse.model_validate(cached)

    preview_rows, matching_count, dataset_total_count, breakdown = await get_filtered_records(
        db, dataset_id, record_filter, preview_limit=_PREVIEW_ROW_LIMIT
    )

    response = DatasetRecordsResponse(
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
    await cache_set_json(cache_key, response.model_dump(mode="json"), ttl_seconds=_RECORDS_CACHE_TTL_SECONDS)
    return response
