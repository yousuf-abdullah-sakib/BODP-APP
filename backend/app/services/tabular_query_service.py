"""PLAN.md Phase 5 (Storage & Query Architecture) — queries Parquet
files directly from MinIO via DuckDB, for DatasetFile rows with
storage_kind=PARQUET. This is the tabular-data half of the routing
layer catalog_service.py/visualize_service.py/admin_qc_service.py each
add in front of their existing DatasetRecord-based query functions;
those existing functions remain completely unmodified for
storage_kind=ROW_RECORDS (legacy) data.

Schema note: every tabular parser's to_processed() writes a WIDE table
(one column per source variable — e.g. time/lat/lon/sea_surface_temp/
salinity), matching the original CSV/NetCDF/.mat shape, NOT the long/
melted (parameter, value) shape DatasetRecord rows used. Melting only
ever happened inside the old _write_dataset_records at SQL-write time —
this module reproduces that same melt at QUERY time instead, via
DuckDB's UNPIVOT, using dataset_file.file_metadata["variables"] (the
parser's own authoritative variable list — identical source
_write_variable_registry already uses) to know which wide columns are
"variables" (unpivoted into parameter/value rows) versus coordinate
columns (time/lat/lon, kept as-is). This keeps the response CONTRACT
(one row per observation x variable) identical to the legacy
DatasetRecord path, without requiring parsers to change their output
shape.

DuckDB's S3/httpfs support was verified empirically against this
project's real MinIO before this module was written (not just reasoned
about from documentation) — a 100K-row Parquet file uploaded to the real
bodp-vps bucket was queried with genuine predicate pushdown confirmed via
EXPLAIN ANALYZE (roughly 2% of the file's bytes fetched for a filtered
COUNT query, 2 GET requests total). See PLAN.md Phase 5's "Guiding
decision" section for the full measured numbers.
"""

import uuid
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any

import duckdb

from app.core.config import settings
from app.models.catalog import DatasetFile, QualityFlag, StorageBackend
from app.services.catalog_service import RecordsFilter


#: DuckDB defaults to using EVERY host CPU thread for a single query
#: (confirmed via `current_setting('threads')` returning the host's full
#: core count). PLAN.md Phase 5's concurrency benchmark measured this
#: directly: at 50 concurrent /catalog/records requests against a real
#: 200K-row Parquet file, backend CPU usage hit ~750-880% (nearly all 12
#: host cores) and p50 latency rose to ~6.8s — each concurrent DuckDB
#: connection was independently trying to claim the whole machine's
#: threads, causing severe CPU oversubscription/queueing rather than any
#: memory or connection-count limit being hit (RAM stayed flat under
#: 1GB throughout). Capping each connection to a small, fixed thread
#: count lets many concurrent short queries share the machine instead of
#: fighting over it. Configurable — re-tested against a real 4-Gunicorn-
#: worker production-mode benchmark alongside query_concurrency.py's
#: per-worker semaphore; even combined, backend CPU still peaked at
#: ~1100% (of 12 cores) under 200+ concurrent requests, meaning DuckDB's
#: httpfs/S3 I/O path uses meaningfully more OS threads than this
#: setting alone bounds — see PLAN.md Phase 5's concurrency benchmark
#: section for the full measured comparison and the honest remaining-
#: limitation writeup.
_DUCKDB_THREADS_PER_CONNECTION = settings.QUERY_DUCKDB_THREADS_PER_CONNECTION


def _configure_connection(con: duckdb.DuckDBPyConnection, *, storage_backend: str) -> None:
    """Points a DuckDB connection's httpfs/S3 settings at the same
    MinIO/cloud endpoint+credentials storage/registry.py already
    resolves for this storage_backend — the exact four config values
    (endpoint, access key, secret key, region) map directly onto
    DuckDB's S3 settings; only the addressing style (path, matching
    S3CompatibleBackend's own boto3 config) and TLS need adding."""
    con.execute(f"SET threads={_DUCKDB_THREADS_PER_CONNECTION}")
    con.install_extension("httpfs")
    con.load_extension("httpfs")

    if storage_backend == StorageBackend.VPS_MINIO.value:
        endpoint = settings.STORAGE_VPS_ENDPOINT_URL
        access_key = settings.STORAGE_VPS_ACCESS_KEY
        secret_key = settings.STORAGE_VPS_SECRET_KEY
        region = settings.STORAGE_VPS_REGION
    elif storage_backend == StorageBackend.CLOUD.value:
        endpoint = settings.STORAGE_CLOUD_ENDPOINT_URL
        access_key = settings.STORAGE_CLOUD_ACCESS_KEY
        secret_key = settings.STORAGE_CLOUD_SECRET_KEY
        region = settings.STORAGE_CLOUD_REGION
    else:
        raise ValueError(f"Unknown storage backend: {storage_backend!r}")

    if not endpoint:
        raise ValueError(f"Storage backend {storage_backend!r} has no endpoint configured")

    # endpoint_url is a full URL (e.g. "http://minio:9000") — DuckDB's
    # s3_endpoint setting wants host[:port] only, and s3_use_ssl controls
    # the scheme separately.
    use_ssl = endpoint.startswith("https://")
    host = endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")

    con.execute(f"SET s3_endpoint='{host}'")
    con.execute(f"SET s3_access_key_id='{access_key}'")
    con.execute(f"SET s3_secret_access_key='{secret_key}'")
    con.execute(f"SET s3_region='{region or 'us-east-1'}'")
    con.execute("SET s3_url_style='path'")
    con.execute(f"SET s3_use_ssl={'true' if use_ssl else 'false'}")


def _connect(*, storage_backend: str) -> duckdb.DuckDBPyConnection:
    """A fresh, short-lived connection per call — DuckDB connections are
    cheap to open (in-process, no server round-trip) and this avoids any
    shared-mutable-state/thread-safety concern across concurrent async
    requests. If the concurrency benchmark (PLAN.md Phase 5 testing
    requirements) shows this is a real bottleneck at 400-500 concurrent
    users, a pooled/reused-connection strategy is the documented next
    step — not assumed correct here without that measurement."""
    con = duckdb.connect(":memory:")
    _configure_connection(con, storage_backend=storage_backend)
    return con


def _load_spatial(con: duckdb.DuckDBPyConnection) -> None:
    con.install_extension("spatial")
    con.load_extension("spatial")


def _parquet_glob(dataset_file: DatasetFile) -> str:
    """DuckDB reads a single Parquet object exactly like a glob of one —
    using the object's own bucket+key rather than a wildcard, since each
    DatasetFile has its own distinct processed/ key (no shared prefix
    convention for Parquet the way Zarr's chunk objects share one)."""
    file_metadata = dataset_file.file_metadata or {}
    bucket = file_metadata.get("processed_bucket")
    key = file_metadata.get("processed_key")
    if not bucket or not key:
        raise ValueError(f"DatasetFile {dataset_file.id} has no processed_key/processed_bucket recorded")
    return f"s3://{bucket}/{key}"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class TabularPreviewRow:
    """Duck-typed equivalent of a DatasetRecord row, for a preview row
    sourced from Parquet via DuckDB rather than the ORM — matches
    DatasetRecordPreview's (schemas/catalog.py) attribute names exactly
    (from_attributes=True lets Pydantic build the response from either
    kind of object interchangeably), and the same attribute set the
    catalog router already reads off each preview row explicitly."""

    id: uuid.UUID
    time: date_type | None
    location: str | None
    depth_m: float | None
    parameter: str
    value: float
    unit: str | None
    platform: str | None
    format: str | None
    processing_level: str | None
    quality_flag: str


@dataclass
class _MeltedTable:
    """Describes how to query a wide Parquet file as if it were the
    melted (time, lat, lon, parameter, value, ...) shape — the SQL
    fragment building blocks _build_melted_query assembles from."""

    glob: str
    variable_columns: list[str]  # the wide columns UNPIVOTed into parameter/value
    coord_columns: set[str]  # time/lat/lon/etc. present in the file, kept as-is
    format_value: str | None  # dataset_file.file_format — DatasetRecord.format's equivalent, constant per file


def _resolve_melted_table(dataset_file: DatasetFile, con: duckdb.DuckDBPyConnection) -> _MeltedTable:
    glob = _parquet_glob(dataset_file)
    file_metadata = dataset_file.file_metadata or {}
    all_variables = list(file_metadata.get("variables") or [])
    lat_col = file_metadata.get("lat_col")
    lon_col = file_metadata.get("lon_col")
    time_col = file_metadata.get("time_col")
    coord_names = {c for c in (lat_col, lon_col, time_col) if c}

    # Same eligibility rule _write_dataset_records used: a "variable"
    # column is one of the parser's own authoritative metadata.variables
    # entries, excluding whichever of those also serves as a coordinate,
    # AND (see _numeric_source_columns' docstring) excluding any column
    # whose Parquet dtype isn't numeric — a text column like "station"
    # is a real, expected metadata.variables entry that the legacy
    # per-row writer silently never turned into rows either.
    numeric_columns = _numeric_source_columns(con, glob)
    variable_columns = [v for v in all_variables if v not in coord_names and v in numeric_columns]

    return _MeltedTable(
        glob=glob,
        variable_columns=variable_columns,
        coord_columns=coord_names,
        format_value=dataset_file.file_format,
    )


def _base_melted_sql(table: _MeltedTable) -> str:
    """The FROM-clause subquery that presents the wide Parquet file as a
    melted (time, lat, lon, parameter, value) relation — every other
    query in this module selects/aggregates/filters FROM this. DuckDB's
    UNPIVOT keeps every non-unpivoted column automatically (coordinate
    columns pass through untouched), so this is a direct translation of
    the wide table, not a hand-built column list."""
    if not table.variable_columns:
        # Nothing to unpivot — an empty relation with the right columns,
        # rather than a DuckDB syntax error on an empty UNPIVOT list.
        return (
            "(SELECT NULL::TIMESTAMP AS time, NULL::DOUBLE AS lat, NULL::DOUBLE AS lon, "
            "NULL::VARCHAR AS parameter, NULL::DOUBLE AS value WHERE FALSE)"
        )

    unpivot_cols = ", ".join(_quote_ident(c) for c in table.variable_columns)
    return (
        f"(UNPIVOT (SELECT * FROM read_parquet('{table.glob}')) "
        f"ON {unpivot_cols} INTO NAME parameter VALUE value)"
    )


def _build_where(f: RecordsFilter, *, columns: set[str]) -> tuple[str, list[Any]]:
    """Translates RecordsFilter into a DuckDB WHERE clause + positional
    parameters, applied against the melted relation _base_melted_sql
    produces — mirrors catalog_service._apply_record_filters's
    predicate-by-predicate shape exactly, so filter semantics stay
    identical between the ROW_RECORDS (SQL) and PARQUET (DuckDB) paths.
    `columns` is the set of columns actually present in the FILE (not
    the melted view) — a station-less tabular file has no "station"
    column at all, etc. — silently skips a filter whose column doesn't
    exist rather than erroring, matching how a DatasetRecord row with a
    NULL column simply never matches an equality filter today.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if f.parameters:
        placeholders = ", ".join("?" for _ in f.parameters)
        clauses.append(f"parameter IN ({placeholders})")
        params.extend(f.parameters)
    if f.quality and "quality_flag" in columns:
        clauses.append("quality_flag = ?")
        params.append(f.quality)
    if f.date_from and "time" in columns:
        clauses.append("time >= ?")
        params.append(f.date_from)
    if f.date_to and "time" in columns:
        clauses.append("time <= ?")
        params.append(f.date_to)
    if f.source and "source" in columns:
        clauses.append("source = ?")
        params.append(f.source)
    if f.platform and "platform" in columns:
        clauses.append("platform = ?")
        params.append(f.platform)
    if f.format_ and "format" in columns:
        clauses.append("format = ?")
        params.append(f.format_)
    if f.processing_level and "processing_level" in columns:
        clauses.append("processing_level = ?")
        params.append(f.processing_level)
    if f.depth_min is not None and "depth_m" in columns:
        clauses.append("depth_m >= ?")
        params.append(f.depth_min)
    if f.depth_max is not None and "depth_m" in columns:
        clauses.append("depth_m <= ?")
        params.append(f.depth_max)
    if f.station and "station" in columns:
        clauses.append("station = ?")
        params.append(f.station)

    # Spatial bounding box: DuckDB's spatial extension ST_Intersects
    # against a constructed envelope — same predicate shape as
    # catalog_service._apply_record_filters's PostGIS ST_Intersects call,
    # verified callable/correct against this project's real DuckDB
    # install before this module was written (see module docstring).
    # Kept as ST_Intersects (not a plain BETWEEN range) for the same
    # reason the SQL path uses it: ready for polygon AOIs later without
    # a query-shape change.
    if None not in (f.lat_min, f.lat_max, f.lon_min, f.lon_max) and {"lat", "lon"} <= columns:
        clauses.append("ST_Intersects(ST_Point(lon, lat), ST_MakeEnvelope(?, ?, ?, ?))")
        params.extend([f.lon_min, f.lat_min, f.lon_max, f.lat_max])

    where_sql = " AND ".join(clauses) if clauses else "TRUE"
    return where_sql, params


_NUMERIC_TYPE_PREFIXES = ("BIGINT", "INTEGER", "DOUBLE", "FLOAT", "DECIMAL", "HUGEINT", "SMALLINT", "TINYINT", "UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT")


def _source_columns(con: duckdb.DuckDBPyConnection, glob: str) -> set[str]:
    result = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{glob}')").fetchall()
    return {row[0] for row in result}


def _numeric_source_columns(con: duckdb.DuckDBPyConnection, glob: str) -> set[str]:
    """The subset of _source_columns whose Parquet dtype is numeric —
    mirrors the legacy _write_dataset_records COPY-writer's per-row
    _is_finite_number(value) skip (ingestion.py), which silently dropped
    any variable-column cell that couldn't cast to float (e.g. a text
    'station' column the parser's own metadata.variables list still
    includes, same as every other non-coordinate column). DuckDB's
    UNPIVOT requires uniform types across every unpivoted column, so a
    text variable column must be excluded from the melt entirely here —
    a whole-column type check is the exact same end result as the
    legacy per-row check, since a genuinely-typed VARCHAR Parquet column
    never has a cell that would have passed _is_finite_number anyway."""
    result = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{glob}')").fetchall()
    return {row[0] for row in result if str(row[1]).upper().startswith(_NUMERIC_TYPE_PREFIXES)}


def _resolution_trunc(resolution: str) -> str:
    """DuckDB's date_trunc part names match Postgres' exactly for every
    resolution Visualize uses except 'seasonal' (a Bangladesh 4-season
    concept with no native trunc unit on either engine). Callers must
    special-case "seasonal" themselves (see get_timeseries_with_counts),
    bucketing day-level rows via visualize_service.season_bucket_date
    instead of using this function's output for that case — this mirrors
    the legacy SQL path's own get_timeseries in visualize_service.py
    exactly, so every storage tier agrees on the same (year, season)
    buckets for resolution="seasonal"."""
    return {"daily": "day", "monthly": "month", "annual": "year"}.get(resolution, "month")


def get_timeseries(
    dataset_file: DatasetFile, parameter: str, f: RecordsFilter, *, resolution: str
) -> list[tuple[date_type, float]]:
    """Mirrors visualize_service.get_timeseries's SQL bucket+avg query, for
    a single PARQUET-backed file and a single parameter — the routing
    layer (visualize_service) merges per-file series across every file
    (and storage kind) a dataset has by summing same-bucket values
    weighted by count, same as it would if all rows were one big SQL
    query. Returns [(bucket_date, avg_value), ...]; the weight (row count
    per bucket) is not returned here — callers needing a true cross-file
    weighted average use get_timeseries_with_counts instead."""
    rows, _ = get_timeseries_with_counts(dataset_file, parameter, f, resolution=resolution)
    return [(bucket, avg) for bucket, avg, _count in rows]


def get_timeseries_with_counts(
    dataset_file: DatasetFile, parameter: str, f: RecordsFilter, *, resolution: str
) -> tuple[list[tuple[date_type, float, int]], set[str]]:
    """Same as get_timeseries but also returns each bucket's row count
    (needed to correctly weight-average buckets that span multiple
    files/storage kinds — a plain mean-of-means would be wrong once more
    than one file contributes to the same bucket) and the set of source
    columns found, for callers that need to know column availability."""
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        _load_spatial(con)
        source_columns = _source_columns(con, table.glob)
        if "time" not in table.coord_columns or parameter not in table.variable_columns:
            return [], source_columns
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=source_columns)

        if resolution == "seasonal":
            # No native SQL trunc unit for the Bangladesh 4-season
            # definition — group by real day-level rows in SQL, then
            # re-bucket into (year, season) in Python via the one
            # canonical helper every storage tier shares.
            from app.services.visualize_service import season_bucket_date

            query = (
                f"SELECT date_trunc('day', time) AS bucket, AVG(value) AS avg_value, "
                f"COUNT(*) AS n FROM {melted_sql} WHERE parameter = ? AND {where_sql} "
                f"GROUP BY bucket ORDER BY bucket"
            )
            rows = con.execute(query, [parameter, *params]).fetchall()
            weighted: dict[date_type, list[float]] = {}
            for bucket, avg, n in rows:
                day = bucket.date() if hasattr(bucket, "date") else bucket
                season_date = season_bucket_date(day)
                total, count = weighted.get(season_date, (0.0, 0))
                weighted[season_date] = (total + float(avg) * n, count + n)
            return [
                (season_date, total / count, int(count))
                for season_date, (total, count) in sorted(weighted.items())
                if count > 0
            ], source_columns

        trunc_unit = _resolution_trunc(resolution)
        query = (
            f"SELECT date_trunc('{trunc_unit}', time) AS bucket, AVG(value) AS avg_value, "
            f"COUNT(*) AS n FROM {melted_sql} WHERE parameter = ? AND {where_sql} "
            f"GROUP BY bucket ORDER BY bucket"
        )
        rows = con.execute(query, [parameter, *params]).fetchall()
        return [
            (bucket.date() if hasattr(bucket, "date") else bucket, float(avg), int(n))
            for bucket, avg, n in rows
        ], source_columns
    finally:
        con.close()


@dataclass
class TabularSpatialPoint:
    station: str
    lat: float
    lon: float
    value: float


def get_spatial_points(
    dataset_file: DatasetFile, parameter: str, f: RecordsFilter
) -> list[TabularSpatialPoint]:
    """Latest value per (lat, lon) location for the given parameter —
    the Parquet-file equivalent of visualize_service.get_spatial_points'
    latest-per-station SQL query. Parquet files carry their own lat/lon
    columns directly rather than a Station foreign key, so "station" here
    is the file's own station/location column if present, else a
    synthesized "lat,lon" label — matches SpatialPointSchema's shape
    (station label + lat/lon + value) either way."""
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        _load_spatial(con)
        source_columns = _source_columns(con, table.glob)
        if not {"lat", "lon"} <= source_columns or parameter not in table.variable_columns:
            return []
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=source_columns)
        has_station = "station" in source_columns
        has_time = "time" in table.coord_columns
        label_expr = "station" if has_station else "CAST(lat AS VARCHAR) || ',' || CAST(lon AS VARCHAR)"
        order_expr = "time DESC" if has_time else "value DESC"
        query = (
            f"SELECT {label_expr} AS label, lat, lon, value FROM {melted_sql} "
            f"WHERE parameter = ? AND {where_sql} "
            f"QUALIFY ROW_NUMBER() OVER (PARTITION BY lat, lon ORDER BY {order_expr}) = 1"
        )
        rows = con.execute(query, [parameter, *params]).fetchall()
        return [
            TabularSpatialPoint(station=str(label), lat=float(lat), lon=float(lon), value=float(value))
            for label, lat, lon, value in rows
        ]
    finally:
        con.close()


def get_raw_values(
    dataset_file: DatasetFile, parameter: str, f: RecordsFilter
) -> list[tuple[str | None, date_type | None, float]]:
    """Every matching (station_label, time, value) triple for one
    parameter in one file — the shared raw-data source for Visualize's
    Comparison (date-aligned series) and Statistics (per-station box
    plot, histogram, decomposition) modules, mirroring the row-level
    detail those modules' SQL queries pull from DatasetRecord directly
    (unlike timeseries' bucket+avg, these need individual observations)."""
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        _load_spatial(con)
        source_columns = _source_columns(con, table.glob)
        if parameter not in table.variable_columns:
            return []
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=source_columns)
        has_station = "station" in source_columns
        has_time = "time" in table.coord_columns
        station_expr = "station" if has_station else "NULL"
        time_expr = "time" if has_time else "NULL"
        query = (
            f"SELECT {station_expr} AS station, {time_expr} AS time, value FROM {melted_sql} "
            f"WHERE parameter = ? AND {where_sql}"
        )
        rows = con.execute(query, [parameter, *params]).fetchall()
        return [
            (station, (t.date() if hasattr(t, "date") else t), float(value))
            for station, t, value in rows
        ]
    finally:
        con.close()


def get_matching_record_counts(dataset_file: DatasetFile, f: RecordsFilter) -> tuple[int, int]:
    """Synchronous — DuckDB's Python client is sync-only; callers on the
    async side (catalog_service.py) run this via asyncio.to_thread so it
    never blocks the event loop. Mirrors catalog_service.
    get_matching_record_counts's return shape exactly: (matching_count,
    dataset_total_count) for THIS ONE FILE — the routing layer sums
    across every PARQUET-backed file in a dataset, same as it sums
    across every ROW_RECORDS-backed file via the existing SQL path.

    dataset_total_count is the MELTED count (rows x variables), matching
    what DatasetRecord's dataset_total_count always meant — one row per
    (observation, variable) pair, not one row per source CSV line.
    """
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        _load_spatial(con)
        source_columns = _source_columns(con, table.glob)
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=source_columns)

        total = con.execute(f"SELECT COUNT(*) FROM {melted_sql}").fetchone()[0]
        matching = con.execute(f"SELECT COUNT(*) FROM {melted_sql} WHERE {where_sql}", params).fetchone()[0]
        return int(matching), int(total)
    finally:
        con.close()


def get_filtered_records(
    dataset_file: DatasetFile, f: RecordsFilter, *, preview_limit: int
) -> tuple[list[TabularPreviewRow], int, int, dict[str, int]]:
    """Mirrors catalog_service.get_filtered_records's return shape
    exactly: (preview_rows, matching_count, dataset_total_count,
    quality_breakdown) for THIS ONE FILE. preview_rows are
    TabularPreviewRow (duck-typed, from_attributes-compatible with the
    DatasetRecordPreview response schema), not real DatasetRecord ORM
    objects — the routing layer merges these with any ROW_RECORDS-backed
    files' real ORM rows into one combined preview list."""
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        _load_spatial(con)
        source_columns = _source_columns(con, table.glob)
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=source_columns)

        total = con.execute(f"SELECT COUNT(*) FROM {melted_sql}").fetchone()[0]
        matching = con.execute(f"SELECT COUNT(*) FROM {melted_sql} WHERE {where_sql}", params).fetchone()[0]

        has_quality = "quality_flag" in source_columns
        quality_breakdown = {"normal": 0, "caution": 0, "alert": 0}
        if has_quality:
            rows = con.execute(
                f"SELECT quality_flag, COUNT(*) FROM {melted_sql} WHERE {where_sql} GROUP BY quality_flag",
                params,
            ).fetchall()
            for flag, count in rows:
                if flag in quality_breakdown:
                    quality_breakdown[flag] = int(count)
        else:
            # No quality_flag column in this file at all — every row is
            # implicitly "normal", matching DatasetRecord's own
            # server-side default when nothing overrides it.
            quality_breakdown["normal"] = int(matching)

        # DatasetRecordPreview (schemas/catalog.py) has no lat/lon fields
        # of its own — only a free-text "location" string — so those
        # aren't selected here even though they exist as coordinate
        # columns in the melted relation; matches what the response
        # schema actually renders.
        optional_cols = [
            ("time", "time" in table.coord_columns),
            ("depth_m", "depth_m" in source_columns),
            ("unit", "unit" in source_columns),
            ("platform", "platform" in source_columns),
            ("format", "format" in source_columns),
            ("processing_level", "processing_level" in source_columns),
            ("quality_flag", has_quality),
            ("location", "location" in source_columns),
        ]
        select_list = ", ".join(
            f"{_quote_ident(name)} AS {name}" if present else f"NULL AS {name}"
            for name, present in optional_cols
        )
        has_time = any(name == "time" and present for name, present in optional_cols)
        order_sql = "ORDER BY time DESC NULLS LAST" if has_time else ""
        preview_rows_raw = con.execute(
            f"SELECT parameter, value, {select_list} FROM {melted_sql} WHERE {where_sql} "
            f"{order_sql} LIMIT {int(preview_limit)}",
            params,
        ).fetchall()

        preview_rows = [
            TabularPreviewRow(
                id=uuid.uuid4(),  # Parquet rows have no stable row id — synthesized for API shape compatibility only
                parameter=row[0],
                value=float(row[1]),
                time=row[2].date() if hasattr(row[2], "date") else row[2],
                depth_m=float(row[3]) if row[3] is not None else None,
                unit=row[4],
                platform=row[5],
                format=row[6] or table.format_value,
                processing_level=row[7],
                quality_flag=row[8] or QualityFlag.NORMAL.value,
                location=row[9],
            )
            for row in preview_rows_raw
        ]

        return preview_rows, int(matching), int(total), quality_breakdown
    finally:
        con.close()


# --- QC detectors (PLAN.md Phase 5 — admin_qc_service.py's Parquet-backed
# equivalent of its three DatasetRecord-based detectors) ---
#
# Each function scans ONE file and returns (has_issue, detail) — the
# routing caller (admin_qc_service.py) iterates every PARQUET-backed
# DatasetFile the same way catalog_service.py's routing layer iterates
# them for filtering, so a dataset with a Parquet file gets genuine QC
# coverage instead of the silent blind spot PLAN.md flagged (these
# detectors query DatasetRecord exclusively, which no longer gets any
# rows from new ingestion).


def detect_duplicates(dataset_file: DatasetFile) -> tuple[bool, str | None]:
    """Same (time, station, parameter) grouping-and-count>1 rule as
    admin_qc_service._detect_duplicates, applied to the melted relation
    so the definition of "one record" (one row per observation x
    variable) matches exactly."""
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        if not table.variable_columns:
            return False, None
        source_columns = _source_columns(con, table.glob)
        melted_sql = _base_melted_sql(table)
        has_time = "time" in table.coord_columns
        has_station = "station" in source_columns
        if not has_time and not has_station:
            # Same rationale as the legacy detector: neither dimension
            # present means grouping can't distinguish real observation
            # slots — nothing to check.
            return False, None
        group_cols = ", ".join(
            c for c, present in (("time", has_time), ("station", has_station), ("parameter", True)) if present
        )
        dimension_present_clauses = [c for c, present in (("time", has_time), ("station", has_station)) if present]
        where_sql = " OR ".join(f"{c} IS NOT NULL" for c in dimension_present_clauses)
        row = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {group_cols}, COUNT(*) AS n FROM {melted_sql} "
            f"WHERE {where_sql} "
            f"GROUP BY {group_cols} HAVING COUNT(*) > 1)"
        ).fetchone()
        dupe_groups = row[0]
        if dupe_groups > 0:
            return True, "Duplicate records found for one or more (time, station, parameter) combinations."
        return False, None
    finally:
        con.close()


def detect_outliers(dataset_file: DatasetFile, *, stddev_multiplier: float) -> tuple[bool, str | None, int]:
    """Same per-(parameter) mean/stddev + >N-stddev-from-mean rule as
    admin_qc_service._detect_outliers, computed in one DuckDB query
    (AVG/STDDEV are native aggregate functions) rather than Python-side
    — avoids pulling the whole melted relation into Python just to
    compute two numbers per parameter."""
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        if not table.variable_columns:
            return False, None, 0
        melted_sql = _base_melted_sql(table)
        row = con.execute(
            f"""
            WITH stats AS (
                SELECT parameter, AVG(value) AS avg_value, STDDEV(value) AS stddev_value
                FROM {melted_sql} GROUP BY parameter
            )
            SELECT COUNT(*) FROM {melted_sql} m
            JOIN stats s ON m.parameter = s.parameter
            WHERE s.stddev_value > 0 AND ABS(m.value - s.avg_value) > ? * s.stddev_value
            """,
            [stddev_multiplier],
        ).fetchone()
        outlier_count = row[0]
        if outlier_count > 0:
            return True, f"{outlier_count} value(s) more than {stddev_multiplier:g} standard deviations from the mean.", outlier_count
        return False, None, 0
    finally:
        con.close()
