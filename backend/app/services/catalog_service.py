import uuid
from datetime import date as date_type

from sqlalchemy import Float, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_get_json, cache_set_json
from app.models.catalog import Dataset, DatasetCategory, DatasetRecord, DatasetStatus, QualityFlag, Station
from app.schemas.catalog import DatasetSort

_TAXONOMY_CACHE_KEY = "catalog:taxonomy"
_TAXONOMY_CACHE_TTL_SECONDS = 300

# Mirrors the prototype's matchScore() weighting exactly (Master Plan §3
# Phase 3 task 1 — "relevance scoring equivalent to the prototype's
# matchScore weighting"). See bodp-frontend/src/app/catalog/useDatasetCatalog.ts.
_SCORE_TITLE_EXACT = 100
_SCORE_TITLE_STARTS_WITH = 60
_SCORE_TITLE_CONTAINS = 40
_SCORE_CODE_CONTAINS = 35
_SCORE_LOCATION_CONTAINS = 20
_SCORE_PARAMETER_CONTAINS = 25
_SCORE_SOURCE_CONTAINS = 15
_SCORE_PLATFORM_CONTAINS = 15
_SCORE_CATEGORY_CONTAINS = 15
_SCORE_DESCRIPTION_CONTAINS = 8


def _relevance_score_expr(query: str):
    """Builds the same weighted score as the prototype's matchScore(), as a
    SQL expression so ranking happens in Postgres rather than in Python —
    scores stack additively exactly like the original (a title match AND a
    parameter match both contribute), title tiers are mutually exclusive
    (exact > starts-with > contains) matching the original if/else if chain.
    """
    like_q = f"%{query}%"

    title_score = case(
        (func.lower(Dataset.title) == query, _SCORE_TITLE_EXACT),
        (func.lower(Dataset.title).like(f"{query}%"), _SCORE_TITLE_STARTS_WITH),
        (func.lower(Dataset.title).like(like_q), _SCORE_TITLE_CONTAINS),
        else_=0,
    )
    code_score = case((func.lower(Dataset.code).like(like_q), _SCORE_CODE_CONTAINS), else_=0)
    location_score = case(
        (func.lower(func.coalesce(Dataset.location, "")).like(like_q), _SCORE_LOCATION_CONTAINS),
        else_=0,
    )
    # Postgres forbids set-returning functions (unnest) directly in a WHERE
    # predicate — they must appear in a FROM clause. column_valued() gives a
    # scalar reference to unnest()'s output column that SQLAlchemy correctly
    # renders as `EXISTS (SELECT 1 FROM unnest(...) AS val WHERE ...)`,
    # unlike the naive `select(1).where(func.unnest(...).like(...))` form
    # (which raises "set-returning functions are not allowed in WHERE").
    param_elem = func.unnest(Dataset.parameters).column_valued("val")
    parameter_score = case(
        (select(1).where(func.lower(param_elem).like(like_q)).exists(), _SCORE_PARAMETER_CONTAINS),
        else_=0,
    )
    source_score = case(
        (func.lower(func.coalesce(Dataset.source, "")).like(like_q), _SCORE_SOURCE_CONTAINS),
        else_=0,
    )
    platform_elem = func.unnest(Dataset.platforms).column_valued("val")
    platform_score = case(
        (select(1).where(func.lower(platform_elem).like(like_q)).exists(), _SCORE_PLATFORM_CONTAINS),
        else_=0,
    )
    description_score = case(
        (
            func.lower(func.coalesce(Dataset.description, "")).like(like_q),
            _SCORE_DESCRIPTION_CONTAINS,
        ),
        else_=0,
    )

    return (
        title_score
        + code_score
        + location_score
        + parameter_score
        + source_score
        + platform_score
        + description_score
    )


async def search_datasets(
    db: AsyncSession,
    *,
    search: str | None,
    category: str | None,
    parameter: str | None,
    source: str | None,
    platform: str | None,
    format_: str | None,
    sort: DatasetSort,
) -> tuple[list[Dataset], int]:
    """GET /catalog/search backing query (Master Plan §3 Phase 3 task 1).

    Only status='published' datasets are ever visible here — draft/archived
    datasets never appear in public results, enforced at the query level so
    no caller can accidentally leak them (Phase 3 quality check).
    """
    query = (
        select(Dataset)
        .options(selectinload(Dataset.category))
        .where(Dataset.status == DatasetStatus.PUBLISHED.value)
    )

    normalized_query = (search or "").strip().lower()

    if normalized_query:
        # A dataset only appears if its score > 0 when a search term is
        # active — matching the prototype's "matchScore(d, q) === 0" filter.
        score_expr = _relevance_score_expr(normalized_query)
        query = query.add_columns(score_expr.label("relevance_score")).where(score_expr > 0)
    else:
        query = query.add_columns(func.cast(0, Float).label("relevance_score"))

    if category:
        query = query.where(Dataset.category.has(DatasetCategory.name == category))
    if parameter:
        query = query.where(Dataset.parameters.any(parameter))
    if source:
        query = query.where(Dataset.source == source)
    if platform:
        query = query.where(Dataset.platforms.any(platform))
    if format_:
        query = query.where(Dataset.formats.any(format_))

    if normalized_query and sort == DatasetSort.RELEVANCE:
        query = query.order_by(_relevance_score_expr(normalized_query).desc(), Dataset.title.asc())
    elif sort == DatasetSort.TITLE:
        query = query.order_by(Dataset.title.asc())
    elif sort == DatasetSort.UPDATED:
        query = query.order_by(Dataset.updated_at.desc())
    elif sort == DatasetSort.RECORDS:
        query = query.order_by(Dataset.record_count.desc())
    else:
        query = query.order_by(Dataset.title.asc())

    result = await db.execute(query)
    rows = result.all()
    datasets = [row[0] for row in rows]
    return datasets, len(datasets)


async def get_published_dataset(db: AsyncSession, dataset_id: uuid.UUID) -> Dataset | None:
    result = await db.execute(
        select(Dataset)
        .options(selectinload(Dataset.category))
        .where(Dataset.id == dataset_id, Dataset.status == DatasetStatus.PUBLISHED.value)
    )
    return result.scalar_one_or_none()


async def get_taxonomy_options(db: AsyncSession) -> dict:
    """Populates catalog filter dropdowns dynamically (Master Plan §3 Phase 3
    task 4), replacing the prototype's hardcoded CATEGORIES/SOURCES/PLATFORMS
    constants. Cached in Redis since this changes rarely but is fetched on
    every catalog page load (task 6)."""
    cached = await cache_get_json(_TAXONOMY_CACHE_KEY)
    if cached is not None:
        return cached

    published = Dataset.status == DatasetStatus.PUBLISHED.value

    categories_result = await db.execute(
        select(DatasetCategory.name)
        .join(Dataset, Dataset.category_id == DatasetCategory.id)
        .where(published)
        .distinct()
        .order_by(DatasetCategory.name)
    )
    categories = [row[0] for row in categories_result.all()]

    parameters_result = await db.execute(
        select(func.distinct(func.unnest(Dataset.parameters))).where(published)
    )
    parameters = sorted(row[0] for row in parameters_result.all())

    sources_result = await db.execute(
        select(Dataset.source).where(published, Dataset.source.is_not(None)).distinct()
    )
    sources = sorted(row[0] for row in sources_result.all())

    platforms_result = await db.execute(
        select(func.distinct(func.unnest(Dataset.platforms))).where(published)
    )
    platforms = sorted(row[0] for row in platforms_result.all())

    formats_result = await db.execute(
        select(func.distinct(func.unnest(Dataset.formats))).where(published)
    )
    formats = sorted(row[0] for row in formats_result.all())

    options = {
        "categories": categories,
        "parameters": parameters,
        "sources": sources,
        "platforms": platforms,
        "formats": formats,
    }
    await cache_set_json(_TAXONOMY_CACHE_KEY, options, ttl_seconds=_TAXONOMY_CACHE_TTL_SECONDS)
    return options


async def get_station_options_for_dataset(
    db: AsyncSession, dataset_id: uuid.UUID
) -> list[Station]:
    """Only stations actually present in this dataset's records — matching
    the prototype's stationOptions (filtered to codes seen in allRecords)."""
    result = await db.execute(
        select(Station)
        .join(DatasetRecord, DatasetRecord.station_id == Station.id)
        .where(DatasetRecord.dataset_id == dataset_id)
        .distinct()
        .order_by(Station.name)
    )
    return list(result.scalars().all())


class RecordsFilter:
    def __init__(
        self,
        *,
        parameter: str | None = None,
        quality: str | None = None,
        date_from: date_type | None = None,
        date_to: date_type | None = None,
        lat_min: float | None = None,
        lat_max: float | None = None,
        lon_min: float | None = None,
        lon_max: float | None = None,
        depth_min: float | None = None,
        depth_max: float | None = None,
        source: str | None = None,
        platform: str | None = None,
        station: str | None = None,
        format_: str | None = None,
        processing_level: str | None = None,
    ):
        self.parameter = parameter
        self.quality = quality
        self.date_from = date_from
        self.date_to = date_to
        self.lat_min = lat_min
        self.lat_max = lat_max
        self.lon_min = lon_min
        self.lon_max = lon_max
        self.depth_min = depth_min
        self.depth_max = depth_max
        self.source = source
        self.platform = platform
        self.station = station
        self.format_ = format_
        self.processing_level = processing_level


def _apply_record_filters(query, dataset_id: uuid.UUID, f: RecordsFilter):
    """Shared WHERE-clause builder for the filtered-records queries — matches
    the prototype's useDatasetFilters filter predicate exactly (Master Plan
    §3 Phase 3 task 3), including bbox-as-ST_Intersects for the spatial case."""
    query = query.where(DatasetRecord.dataset_id == dataset_id)

    if f.parameter:
        query = query.where(DatasetRecord.parameter == f.parameter)
    if f.quality:
        query = query.where(DatasetRecord.quality_flag == f.quality)
    if f.date_from:
        query = query.where(DatasetRecord.time >= f.date_from)
    if f.date_to:
        query = query.where(DatasetRecord.time <= f.date_to)
    if f.source:
        query = query.where(DatasetRecord.source == f.source)
    if f.platform:
        query = query.where(DatasetRecord.platform == f.platform)
    if f.format_:
        query = query.where(DatasetRecord.format == f.format_)
    if f.processing_level:
        query = query.where(DatasetRecord.processing_level == f.processing_level)
    if f.depth_min is not None:
        query = query.where(DatasetRecord.depth_m >= f.depth_min)
    if f.depth_max is not None:
        query = query.where(DatasetRecord.depth_m <= f.depth_max)

    if f.station:
        query = query.where(Station.code == f.station).where(
            DatasetRecord.station_id == Station.id
        )

    # Spatial bounding box: PostGIS ST_Intersects against a constructed
    # envelope (Master Plan §3 Phase 3 task 3 — "bbox/polygon via PostGIS
    # ST_Intersects"), rather than plain lat/lon BETWEEN comparisons, so the
    # same code path is ready for polygon AOIs later (ST_Intersects accepts
    # any geometry) without a query-shape change.
    if None not in (f.lat_min, f.lat_max, f.lon_min, f.lon_max):
        envelope = func.ST_MakeEnvelope(f.lon_min, f.lat_min, f.lon_max, f.lat_max, 4326)
        query = query.where(func.ST_Intersects(DatasetRecord.geom, envelope))

    return query


async def get_filtered_records(
    db: AsyncSession, dataset_id: uuid.UUID, f: RecordsFilter, *, preview_limit: int
) -> tuple[list[DatasetRecord], int, int, dict[str, int]]:
    """Returns (preview_rows, matching_count, dataset_total_count,
    quality_breakdown) for the /catalog/{id}/records endpoint (Master Plan
    §3 Phase 3 task 3)."""
    base_query = select(DatasetRecord)
    if f.station:
        base_query = base_query.join(Station, Station.id == DatasetRecord.station_id)
    filtered_query = _apply_record_filters(base_query, dataset_id, f)

    count_query = select(func.count()).select_from(filtered_query.subquery())
    matching_count = (await db.execute(count_query)).scalar_one()

    total_query = select(func.count()).select_from(
        select(DatasetRecord).where(DatasetRecord.dataset_id == dataset_id).subquery()
    )
    dataset_total_count = (await db.execute(total_query)).scalar_one()

    # Rebuild the breakdown from the same filter predicates via a fresh
    # grouped query (re-applying _apply_record_filters keeps this in sync
    # with the main filtered_query without duplicating the predicate list).
    breakdown_base = _apply_record_filters(
        select(DatasetRecord.quality_flag, func.count().label("cnt")).group_by(
            DatasetRecord.quality_flag
        )
        if not f.station
        else select(DatasetRecord.quality_flag, func.count().label("cnt"))
        .join(Station, Station.id == DatasetRecord.station_id)
        .group_by(DatasetRecord.quality_flag),
        dataset_id,
        f,
    )
    breakdown_result = await db.execute(breakdown_base)
    breakdown_rows = {row[0]: row[1] for row in breakdown_result.all()}
    quality_breakdown = {
        "normal": breakdown_rows.get(QualityFlag.NORMAL.value, 0),
        "caution": breakdown_rows.get(QualityFlag.CAUTION.value, 0),
        "alert": breakdown_rows.get(QualityFlag.ALERT.value, 0),
    }

    preview_query = filtered_query.order_by(DatasetRecord.time.desc()).limit(preview_limit)
    preview_result = await db.execute(preview_query)
    preview_rows = list(preview_result.scalars().all())

    return preview_rows, matching_count, dataset_total_count, quality_breakdown
