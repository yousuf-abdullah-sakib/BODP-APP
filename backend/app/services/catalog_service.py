import uuid
from datetime import date as date_type

from sqlalchemy import Float, case, func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_get_json, cache_set_json
from app.models.catalog import (
    Dataset,
    DatasetCategory,
    DatasetFile,
    DatasetRecord,
    DatasetStatus,
    DatasetVariable,
    QualityFlag,
    Station,
    StorageKind,
)
from app.schemas.catalog import DatasetSchemaFilters, DatasetSort, SchemaFilterVariable
from app.services.query_concurrency import bounded_to_thread

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


async def get_dataset_schema_for_filters(
    db: AsyncSession, dataset_id: uuid.UUID
) -> DatasetSchemaFilters | None:
    """Backs GET /catalog/{id}/schema (PLAN.md Phase 4) — the fallback
    trigger for schema-driven filter rendering. Returns None for a dataset
    an admin hasn't reviewed yet (Dataset.schema_reviewed_at IS NULL), so
    the frontend keeps today's fixed dataset.parameters-driven rendering
    for anything not yet reviewed, per the explicit no-hard-cutover
    decision. Only variables an admin actually assigned a role to (Phase
    3) are returned — an unreviewed leftover with empty roles never
    surfaces here even on an otherwise-reviewed dataset."""
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None or dataset.schema_reviewed_at is None:
        return None

    result = await db.execute(
        select(DatasetVariable)
        .where(
            DatasetVariable.dataset_id == dataset_id,
            func.cardinality(DatasetVariable.roles) > 0,
        )
        .order_by(DatasetVariable.name)
    )
    variables = list(result.scalars().all())

    return DatasetSchemaFilters(
        reviewed_at=dataset.schema_reviewed_at,
        variables=[
            SchemaFilterVariable(
                name=v.name,
                data_type=v.data_type,
                is_dimension=v.is_dimension,
                roles=v.roles,
                min_value=float(v.min_value) if v.min_value is not None else None,
                max_value=float(v.max_value) if v.max_value is not None else None,
                distinct_values=v.distinct_values,
            )
            for v in variables
        ],
    )


class RecordsFilter:
    def __init__(
        self,
        *,
        parameters: list[str] | None = None,
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
        self.parameters = parameters
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
    §3 Phase 3 task 3), including bbox-as-ST_Intersects for the spatial case.

    parameters is a list (checkbox multi-select on the dataset detail
    page) — zero/None selected means no filtering by parameter at all (all
    approved parameters included), matching a plain unchecked state;
    non-empty applies an IN filter covering one or many selected values."""
    query = query.where(DatasetRecord.dataset_id == dataset_id)

    if f.parameters:
        query = query.where(DatasetRecord.parameter.in_(f.parameters))
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


async def _get_matching_record_counts_sql(
    db: AsyncSession, dataset_id: uuid.UUID, f: RecordsFilter
) -> tuple[int, int]:
    """Legacy path — queries DatasetRecord SQL rows, exactly as before
    PLAN.md Phase 5. Untouched: still correct and still the only path for
    storage_kind=ROW_RECORDS files, which is every file ingested before
    Phase 5's rollout. Never called directly by external code anymore —
    get_matching_record_counts (below) is the public entry point, which
    calls this only for a dataset's ROW_RECORDS-backed portion."""
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

    return matching_count, dataset_total_count


async def _get_filtered_records_sql(
    db: AsyncSession, dataset_id: uuid.UUID, f: RecordsFilter, *, preview_limit: int
) -> tuple[list[DatasetRecord], int, int, dict[str, int]]:
    """Legacy path — see _get_matching_record_counts_sql's docstring;
    same relationship to get_filtered_records (below)."""
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

    # Phase 2: DatasetRecord.time is now nullable (not every dataset has a
    # time dimension) — Postgres defaults DESC to NULLS FIRST, which would
    # put every timeless row ahead of real dates in the preview. Timeless
    # rows are still real data and still returned, just ordered after
    # anything with an actual date.
    preview_query = filtered_query.order_by(nulls_last(DatasetRecord.time.desc())).limit(preview_limit)
    preview_result = await db.execute(preview_query)
    preview_rows = list(preview_result.scalars().all())

    return preview_rows, matching_count, dataset_total_count, quality_breakdown


async def _non_legacy_dataset_files(db: AsyncSession, dataset_id: uuid.UUID) -> dict[str, list[DatasetFile]]:
    """Returns only a dataset's PARQUET/CHUNKED_ARRAY DatasetFile rows,
    grouped by storage_kind — the ADDITIVE query targets Phase 5 routing
    layers on top of the always-run legacy SQL path (see
    get_matching_record_counts/get_filtered_records below for why this
    is additive-only, never a replacement for the SQL query).

    Deliberately does NOT attempt to enumerate ROW_RECORDS files at all
    — DatasetRecord rows are not guaranteed to trace back to a
    DatasetFile row in the first place (e.g. seed_catalog.py's demo data,
    and every existing test fixture that seeds DatasetRecord rows
    directly without a DatasetFile) — the SQL path's own dataset_id-scoped
    query already finds them correctly and unconditionally, exactly as it
    did before Phase 5 existed."""
    result = await db.execute(
        select(DatasetFile).where(
            DatasetFile.dataset_id == dataset_id,
            DatasetFile.storage_kind.in_([StorageKind.PARQUET.value, StorageKind.CHUNKED_ARRAY.value]),
        )
    )
    files = list(result.scalars().all())
    grouped: dict[str, list[DatasetFile]] = {}
    for file in files:
        grouped.setdefault(file.storage_kind, []).append(file)
    return grouped


async def get_matching_record_counts(
    db: AsyncSession, dataset_id: uuid.UUID, f: RecordsFilter
) -> tuple[int, int]:
    """Returns (matching_count, dataset_total_count) only — no preview rows
    or quality breakdown — for callers that just need the coverage numbers
    (e.g. the admin request queue's "X% of dataset" figure) without the
    cost of a full get_filtered_records call per request.

    PLAN.md Phase 5 routing: the legacy SQL path runs UNCONDITIONALLY
    (exactly as it always did — DatasetRecord rows are not guaranteed to
    trace back to a DatasetFile row, so "does this dataset have a
    ROW_RECORDS-kind file" is not a safe gate for whether SQL rows
    exist), with PARQUET (DuckDB) and CHUNKED_ARRAY (xarray/Zarr) files
    summed in additively on top. A dataset with files of more than one
    kind (a real, expected state — the old and new architectures run in
    parallel, see PLAN.md's "What happens to existing DatasetRecord
    data") gets a genuinely combined total, never silently dropping one
    kind's contribution."""
    sql_matching, sql_total = await _get_matching_record_counts_sql(db, dataset_id, f)
    matching_total = sql_matching
    dataset_total = sql_total

    grouped = await _non_legacy_dataset_files(db, dataset_id)
    for file in grouped.get(StorageKind.PARQUET.value, []):
        from app.services import tabular_query_service

        file_matching, file_total = await bounded_to_thread(
            tabular_query_service.get_matching_record_counts, file, f
        )
        matching_total += file_matching
        dataset_total += file_total

    for file in grouped.get(StorageKind.CHUNKED_ARRAY.value, []):
        from app.services import gridded_query_service

        file_matching, file_total = await bounded_to_thread(
            gridded_query_service.get_matching_record_counts, file, f
        )
        matching_total += file_matching
        dataset_total += file_total

    return matching_total, dataset_total


async def get_filtered_records(
    db: AsyncSession, dataset_id: uuid.UUID, f: RecordsFilter, *, preview_limit: int
) -> tuple[list, int, int, dict[str, int]]:
    """Returns (preview_rows, matching_count, dataset_total_count,
    quality_breakdown) for the /catalog/{id}/records endpoint.

    PLAN.md Phase 5 routing: combines every storage kind a dataset's
    files actually use, same as get_matching_record_counts. preview_rows
    may be a mix of real DatasetRecord ORM objects (ROW_RECORDS) and
    duck-typed TabularPreviewRow/GriddedPreviewRow objects (PARQUET/
    CHUNKED_ARRAY) — the router (routers/catalog.py) already reads every
    preview row via plain attribute access (r.id, r.time, ...), which
    works identically regardless of the concrete type, so no caller-side
    change is needed for this to be transparent. Combined preview is
    capped at preview_limit total (not preview_limit per storage kind),
    ordered the same way each source already orders its own rows
    (most-recent-first) — cross-source interleaving beyond that is not
    attempted, since the existing single-source order already isn't a
    strict global time-sort guarantee for ROW_RECORDS files spanning
    multiple DatasetFiles either.

    Like get_matching_record_counts, the legacy SQL path runs
    UNCONDITIONALLY — see that function's docstring for why "does this
    dataset have a ROW_RECORDS-kind DatasetFile" is not a safe gate.
    """
    sql_preview, sql_matching, sql_total, sql_breakdown = await _get_filtered_records_sql(
        db, dataset_id, f, preview_limit=preview_limit
    )
    all_preview: list = list(sql_preview)
    matching_total = sql_matching
    dataset_total = sql_total
    quality_breakdown = {
        "normal": sql_breakdown.get("normal", 0),
        "caution": sql_breakdown.get("caution", 0),
        "alert": sql_breakdown.get("alert", 0),
    }

    grouped = await _non_legacy_dataset_files(db, dataset_id)
    for file in grouped.get(StorageKind.PARQUET.value, []):
        from app.services import tabular_query_service

        file_preview, file_matching, file_total, file_breakdown = await bounded_to_thread(
            tabular_query_service.get_filtered_records, file, f, preview_limit=preview_limit
        )
        all_preview.extend(file_preview)
        matching_total += file_matching
        dataset_total += file_total
        for key in quality_breakdown:
            quality_breakdown[key] += file_breakdown.get(key, 0)

    for file in grouped.get(StorageKind.CHUNKED_ARRAY.value, []):
        from app.services import gridded_query_service

        file_preview, file_matching, file_total, file_breakdown = await bounded_to_thread(
            gridded_query_service.get_filtered_records, file, f, preview_limit=preview_limit
        )
        all_preview.extend(file_preview)
        matching_total += file_matching
        dataset_total += file_total
        for key in quality_breakdown:
            quality_breakdown[key] += file_breakdown.get(key, 0)

    return all_preview[:preview_limit], matching_total, dataset_total, quality_breakdown
