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
from datetime import timedelta
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
    # Which of the canonical roles ("time"/"lat"/"lon") this file actually
    # has — always the CANONICAL label, never the file's own raw column
    # name (see raw_lat_col/raw_lon_col/raw_time_col below for that).
    # _base_melted_sql aliases whichever raw columns exist to these exact
    # canonical names, so every other query in this module can safely
    # reference literal "time"/"lat"/"lon" and be correct regardless of
    # what the source file actually called them.
    coord_columns: set[str]
    format_value: str | None  # dataset_file.file_format — DatasetRecord.format's equivalent, constant per file
    # The file's OWN coordinate column names, as detected by the parser at
    # ingestion time (file_metadata["lat_col"]/["lon_col"]/["time_col"]) —
    # NOT necessarily the literal strings "lat"/"lon"/"time". Parsers
    # detect which column plays each coordinate role (accepting aliases
    # like "latitude"/"Lat"/"y") but never rename the column when writing
    # the wide Parquet file, so a file's real columns can be anything.
    # Used ONLY by _base_melted_sql to build the aliasing SELECT below —
    # no other function should reference these raw names directly.
    raw_lat_col: str | None
    raw_lon_col: str | None
    raw_time_col: str | None
    # Oceanographic Profiles module: the file's own detected depth/vertical
    # coordinate column name (e.g. "depth", not necessarily "depth_m") —
    # same raw/canonical split as lat/lon/time above. None for a file with
    # no detected depth column, same as those.
    raw_depth_col: str | None = None
    # True when raw_time_col's real Parquet dtype is numeric AND the
    # source file is a MATLAB (.mat) file — see _coordinate_alias_sql's
    # docstring. Distinguishes "this numeric time column is a MATLAB
    # datenum needing epoch conversion" from "this time column is
    # already a proper TIMESTAMP/DATE" (False) — never set for a VARCHAR
    # ISO-date column either, which the plain CAST branch already
    # handles correctly on its own.
    raw_time_is_datenum: bool = False


def _resolve_coord_col(
    file_metadata: dict, col_key: str, coord_key: str, canonical_name: str, source_columns: set[str]
) -> str | None:
    """Coordinate-column detection has three fallback tiers, oldest
    legacy metadata dialect last — confirmed by direct inspection of
    every real DatasetFile.file_metadata dict in the dev database
    (Visualization & Filter Reliability investigation, issue #1/#8):

    1. lat_col/lon_col/time_col — the current parser's own metadata key,
       recording the file's real column name for that role even when
       it's non-canonical (e.g. "latitude").
    2. lat_coord/lon_coord/time_coord — an older NetCDF/GRIB-derived
       parser format found on several pre-Phase-5 files (e.g. "Model
       Wave Data", "Coastal Wind & Precipitation"), using a different
       key suffix for the same concept.
    3. The literal canonical name ("lat"/"lon"/"time"), when NEITHER key
       above is present at all AND a column with that exact name exists
       in the file's real Parquet schema — the oldest legacy files (e.g.
       "Bay of Bengal SST", "Sundarbans Salinity Dynamics") never
       recorded a coordinate-column mapping in file_metadata, but happen
       to already use canonical column names. This tier only ever
       matches an exact name — it never guesses at an approximate one
       (e.g. "valid_time"), so a file whose time column is genuinely
       unrecorded and non-canonical correctly still resolves to None
       rather than a fabricated guess.

    Returns None (never fabricates) when no tier finds a match — the
    caller treats that exactly like "this file has no such coordinate".
    """
    val = file_metadata.get(col_key) or file_metadata.get(coord_key)
    if val:
        return val
    if canonical_name in source_columns:
        return canonical_name
    return None


def _resolve_melted_table(dataset_file: DatasetFile, con: duckdb.DuckDBPyConnection) -> _MeltedTable:
    glob = _parquet_glob(dataset_file)
    file_metadata = dataset_file.file_metadata or {}
    all_variables = list(file_metadata.get("variables") or [])
    source_columns = _source_columns(con, glob)
    lat_col = _resolve_coord_col(file_metadata, "lat_col", "lat_coord", "lat", source_columns)
    lon_col = _resolve_coord_col(file_metadata, "lon_col", "lon_coord", "lon", source_columns)
    time_col = _resolve_coord_col(file_metadata, "time_col", "time_coord", "time", source_columns)
    # No legacy "depth_coord" dialect exists (depth detection is new, added
    # for the Oceanographic Profiles module — there is no pre-Phase-5 file
    # to be backward-compatible with here), so this only has the two tiers
    # _resolve_coord_col already supports when its second key is absent
    # from file_metadata: depth_col, then literal "depth_m" match.
    depth_col = _resolve_coord_col(file_metadata, "depth_col", "depth_col", "depth_m", source_columns)
    coord_names = {c for c in (lat_col, lon_col, time_col, depth_col) if c}

    # Same eligibility rule _write_dataset_records used: a "variable"
    # column is one of the parser's own authoritative metadata.variables
    # entries, excluding whichever of those also serves as a coordinate,
    # AND (see _numeric_source_columns' docstring) excluding any column
    # whose Parquet dtype isn't numeric — a text column like "station"
    # is a real, expected metadata.variables entry that the legacy
    # per-row writer silently never turned into rows either.
    numeric_columns = _numeric_source_columns(con, glob)
    variable_columns = [v for v in all_variables if v not in coord_names and v in numeric_columns]

    # A MATLAB (.mat) source file's time column, if numeric, is a
    # datenum needing epoch conversion rather than a plain dtype CAST
    # (see _coordinate_alias_sql) — confirmed via direct reproduction:
    # scipy.io.loadmat/h5py never carry a datetime dtype, only floats,
    # and pre-fix ingestion wrote that raw datenum straight to Parquet
    # (now fixed at the source for new uploads in mat_parser.py, but
    # already-ingested files still have the raw numeric column).
    raw_time_is_datenum = bool(time_col) and time_col in numeric_columns and dataset_file.file_format == "mat"

    return _MeltedTable(
        glob=glob,
        variable_columns=variable_columns,
        coord_columns={
            canonical
            for canonical, raw in (
                ("lat", lat_col), ("lon", lon_col), ("time", time_col), ("depth_m", depth_col)
            )
            if raw
        },
        format_value=dataset_file.file_format,
        raw_lat_col=lat_col,
        raw_lon_col=lon_col,
        raw_time_col=time_col,
        raw_depth_col=depth_col,
        raw_time_is_datenum=raw_time_is_datenum,
    )


def _coordinate_alias_sql(table: _MeltedTable) -> str:
    """SELECT fragment that renames whichever of the file's own lat/lon/
    time columns exist to the canonical "lat"/"lon"/"time" names, and
    drops the original-named columns (EXCLUDE) so there's no duplicate —
    e.g. a file whose real longitude column is named "longitude" ends up
    with a "lon" column instead, never both. Columns the file doesn't
    have are simply not mentioned (no NULL padding needed — downstream
    "'lat' in table.coord_columns"-style checks already gate on absence).

    The time column is additionally normalized to TIMESTAMP, one of two
    ways depending on what's actually stored (both confirmed via direct
    reproduction against real datasets, not assumed):

    - CAST(... AS TIMESTAMP) — several pre-Phase-5 legacy files' Parquet
      time column is stored as VARCHAR (clean ISO date strings, e.g.
      "2022-01-01", written by an older ingestion code path that
      predates today's parser's pd.to_datetime() coercion at
      Parquet-write time), which every date-comparison query in this
      module (time >= ?, date_trunc(...), etc.) then fails on with a
      DuckDB BinderException. CAST is a safe no-op when the column is
      already a proper temporal type, and correctly parses a clean
      ISO-format string when it isn't.
    - Epoch-offset arithmetic (table.raw_time_is_datenum) — a MATLAB
      (.mat) source file's time column, when numeric, is a datenum (days
      since year 0, proleptic), which a blind CAST cannot handle at all
      (DuckDB has no DOUBLE->TIMESTAMP cast — confirmed: "Conversion
      Error: Unimplemented type for cast (DOUBLE -> TIMESTAMP)").
      719529 is the datenum for the Unix epoch (1970-01-01) — the same
      constant/formula already used and proven correct in mat_parser.py
      (now also applied at ingestion time for new uploads there; this
      branch only exists for files ingested before that fix).

    Neither branch is a new independent date-parsing implementation —
    both are dtype/encoding normalization of an already-real column,
    using formulas already established elsewhere in this codebase."""
    raw_names = [
        c for c in (table.raw_lat_col, table.raw_lon_col, table.raw_time_col, table.raw_depth_col) if c
    ]
    if not raw_names:
        return "*"
    exclude_sql = ", ".join(_quote_ident(c) for c in raw_names)
    aliases = []
    if table.raw_lat_col:
        aliases.append(f"{_quote_ident(table.raw_lat_col)} AS lat")
    if table.raw_lon_col:
        aliases.append(f"{_quote_ident(table.raw_lon_col)} AS lon")
    if table.raw_depth_col:
        # Already canonically named "depth_m" for a legacy file with no
        # recorded depth_col (the literal-name fallback tier), in which
        # case this EXCLUDE+re-alias is a harmless no-op self-rename.
        aliases.append(f"{_quote_ident(table.raw_depth_col)} AS depth_m")
    if table.raw_time_col:
        quoted_time = _quote_ident(table.raw_time_col)
        if table.raw_time_is_datenum:
            aliases.append(f"(TIMESTAMP '1970-01-01' + ({quoted_time} - 719529) * INTERVAL 1 DAY) AS time")
        else:
            aliases.append(f"CAST({quoted_time} AS TIMESTAMP) AS time")
    return f"* EXCLUDE ({exclude_sql}), " + ", ".join(aliases)


def _base_melted_sql(table: _MeltedTable) -> str:
    """The FROM-clause subquery that presents the wide Parquet file as a
    melted (time, lat, lon, parameter, value) relation — every other
    query in this module selects/aggregates/filters FROM this. DuckDB's
    UNPIVOT keeps every non-unpivoted column automatically (coordinate
    columns pass through untouched), so this is a direct translation of
    the wide table plus the canonical coordinate aliasing above, not a
    hand-built column list."""
    if not table.variable_columns:
        # Nothing to unpivot — an empty relation with the right columns,
        # rather than a DuckDB syntax error on an empty UNPIVOT list.
        return (
            "(SELECT NULL::TIMESTAMP AS time, NULL::DOUBLE AS lat, NULL::DOUBLE AS lon, "
            "NULL::VARCHAR AS parameter, NULL::DOUBLE AS value WHERE FALSE)"
        )

    unpivot_cols = ", ".join(_quote_ident(c) for c in table.variable_columns)
    select_cols = _coordinate_alias_sql(table)
    return (
        f"(UNPIVOT (SELECT {select_cols} FROM read_parquet('{table.glob}')) "
        f"ON {unpivot_cols} INTO NAME parameter VALUE value)"
    )


def _melted_columns(table: _MeltedTable, source_columns: set[str]) -> set[str]:
    """The column set as it actually appears in the MELTED view (after
    _base_melted_sql's coordinate aliasing) — NOT the raw file's own
    column names. Every non-coordinate column (station, quality_flag,
    depth_m, ...) passes through unchanged, so those are taken straight
    from source_columns; lat/lon/time are taken from table.coord_columns
    (canonical labels) instead, since the raw file's own names for those
    (if different, e.g. "latitude") no longer exist as columns after
    aliasing. Callers that need to know whether a filterable predicate's
    column exists in the melted view (_build_where, get_spatial_points)
    must use this, not source_columns directly, for lat/lon/time."""
    raw_coord_names = {table.raw_lat_col, table.raw_lon_col, table.raw_time_col, table.raw_depth_col} - {None}
    return (source_columns - raw_coord_names) | table.coord_columns


def _build_where(f: RecordsFilter, *, columns: set[str]) -> tuple[str, list[Any]]:
    """Translates RecordsFilter into a DuckDB WHERE clause + positional
    parameters, applied against the melted relation _base_melted_sql
    produces — mirrors catalog_service._apply_record_filters's
    predicate-by-predicate shape exactly, so filter semantics stay
    identical between the ROW_RECORDS (SQL) and PARQUET (DuckDB) paths.
    `columns` must be the melted view's own column set (see
    _melted_columns), not the raw file's columns — a station-less
    tabular file has no "station" column at all, etc. — silently skips
    a filter whose column doesn't exist rather than erroring, matching
    how a DatasetRecord row with a NULL column simply never matches an
    equality filter today.
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
        # Exclusive upper bound at the START of the NEXT day, not "<=
        # date_to" -- unlike the legacy path (DatasetRecord.time is a
        # plain Date column with no time-of-day component, so <= is
        # already correct there), this module's "time" column can be a
        # genuine TIMESTAMP with sub-day granularity (e.g. 6-hourly
        # gridded/tabular data). A naive "time <= date_to" implicitly
        # compares against date_to's midnight, silently excluding every
        # same-day row after 00:00:00 -- confirmed via direct
        # reproduction against a real 6-hourly dataset. xarray's
        # equivalent date-range slice (gridded_query_service.py's
        # _apply_date_range) already gets this right for free via
        # pandas' partial-string-indexing semantics (verified
        # separately); DuckDB's plain parameterized comparison does not,
        # so it needs the explicit +1-day boundary here instead.
        clauses.append("time < ?")
        params.append(f.date_to + timedelta(days=1))
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
        where_sql, params = _build_where(f, columns=_melted_columns(table, source_columns))

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
        if not {"lat", "lon"} <= table.coord_columns or parameter not in table.variable_columns:
            return []
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=_melted_columns(table, source_columns))
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
            if _is_valid_coordinate(lat, lon)
        ]
    finally:
        con.close()


def _is_valid_coordinate(lat: Any, lon: Any) -> bool:
    """Excludes a raw file-sourced coordinate pair that's missing, NaN, or
    out of the real WGS84 range — unlike the legacy path (Station.lat/lon
    are non-nullable DB columns, always valid by construction), Parquet
    lat/lon values come straight from uploaded file data with no
    validation at ingestion time. Geographic convention only (longitude
    is X, latitude is Y) — no CRS transformation here, this only filters
    already-computed values, never re-projects them."""
    if lat is None or lon is None:
        return False
    lat_f, lon_f = float(lat), float(lon)
    if lat_f != lat_f or lon_f != lon_f:  # NaN != NaN
        return False
    return -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0


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
        where_sql, params = _build_where(f, columns=_melted_columns(table, source_columns))
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


def get_raw_values_with_location(
    dataset_file: DatasetFile, parameter: str, f: RecordsFilter
) -> list[tuple[float | None, float | None, float]]:
    """Every matching (lat, lon, value) triple for one parameter in one
    file — Visualize Module audit fix (Multivariable — Non-Temporal
    Datasets): a SEPARATE function from get_raw_values above, not an
    added return column on it, specifically so Statistics' existing
    (station, time, value)-tuple unpacking of get_raw_values is never put
    at risk by this change (Statistics is explicitly out of scope for
    this fix).

    Used only as get_comparison's fallback pairing key when a dataset has
    no temporal dimension at all: two parameters' observations are paired
    by matching (lat, lon) instead of matching date, which is exactly as
    scientifically valid a join key for Pearson correlation/regression —
    the only real requirement is that x[i] and y[i] come from the same
    underlying observation, not that the shared key be a timestamp.
    lat/lon are already selectable as canonical-aliased columns from
    _base_melted_sql for ANY file (NULL when absent), the same way
    get_spatial_points above already reads them — no new column
    detection needed.

    Rows with a NULL lat or NULL lon are excluded (same principle
    get_raw_values applies to NULL time: an unpaired-key observation
    cannot be joined to anything and must not silently collapse into a
    single NULL-keyed bucket with unrelated rows)."""
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        _load_spatial(con)
        source_columns = _source_columns(con, table.glob)
        if not {"lat", "lon"} <= table.coord_columns or parameter not in table.variable_columns:
            return []
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=_melted_columns(table, source_columns))
        query = (
            f"SELECT lat, lon, value FROM {melted_sql} "
            f"WHERE parameter = ? AND lat IS NOT NULL AND lon IS NOT NULL AND {where_sql}"
        )
        rows = con.execute(query, [parameter, *params]).fetchall()
        return [(float(lat), float(lon), float(value)) for lat, lon, value in rows]
    finally:
        con.close()


@dataclass
class ProfilePoint:
    depth_m: float
    value: float


@dataclass
class StationProfile:
    station: str | None
    lat: float | None
    lon: float | None
    time: date_type | None
    points: list[ProfilePoint]


def dataset_file_has_depth_dimension(dataset_file: DatasetFile) -> bool:
    """Cheap existence check — does this file's own metadata carry a
    detected depth/vertical coordinate at all? Mirrors visualize_service.
    dataset_has_temporal_dimension's role for the Oceanographic Profiles
    module (see that function's docstring for the parallel pattern), but
    at the single-file granularity this module's other functions already
    operate at, since a dataset can mix depth-resolved and non-depth-
    resolved files across its DatasetFile rows."""
    file_metadata = dataset_file.file_metadata or {}
    return bool(file_metadata.get("depth_col"))


def get_profile(
    dataset_file: DatasetFile, parameter: str, f: RecordsFilter
) -> list[StationProfile]:
    """One vertical profile (depth-sorted (depth, value) points) per
    distinct station+time combination matching the filter — the shared
    query the Oceanographic Profiles module's single-variable Depth
    profile charts (Temperature/Salinity/Density/Sound Speed/Pressure vs
    Depth) are built from.

    Depth-null rows are excluded (a row with a value but no depth cannot
    be placed on a vertical axis) — mirrors get_raw_values's existing
    "unusable dimension" exclusion for time-null rows. Duplicate exact
    (station, time, depth) triples keep the LAST-seen value only, matching
    this codebase's existing idempotency convention (see
    _write_dataset_records's "retry of the same DatasetFile is exactly-
    once" docstring) — never averaged silently, never both kept as if
    they were two genuinely different observations.
    """
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        _load_spatial(con)
        source_columns = _source_columns(con, table.glob)
        if "depth_m" not in table.coord_columns or parameter not in table.variable_columns:
            return []
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=_melted_columns(table, source_columns))
        has_station = "station" in source_columns
        has_time = "time" in table.coord_columns
        station_expr = "station" if has_station else "NULL"
        time_expr = "time" if has_time else "NULL"
        query = (
            f"SELECT {station_expr} AS station, lat, lon, {time_expr} AS time, depth_m, value "
            f"FROM {melted_sql} WHERE parameter = ? AND depth_m IS NOT NULL AND {where_sql}"
        )
        rows = con.execute(query, [parameter, *params]).fetchall()

        # Group into one StationProfile per (station, time) — last-value-
        # wins for an exact duplicate depth within the same group, applied
        # via plain dict-overwrite-on-iteration (insertion order from the
        # query result is the only ordering that matters for "last").
        groups: dict[tuple, dict] = {}
        for station, lat, lon, t, depth_m, value in rows:
            time_val = t.date() if hasattr(t, "date") else t
            key = (station, time_val)
            group = groups.setdefault(
                key,
                {"station": station, "lat": lat, "lon": lon, "time": time_val, "depths": {}},
            )
            if lat is not None and group["lat"] is None:
                group["lat"] = float(lat)
            if lon is not None and group["lon"] is None:
                group["lon"] = float(lon)
            group["depths"][float(depth_m)] = float(value)

        return [
            StationProfile(
                station=g["station"],
                lat=g["lat"],
                lon=g["lon"],
                time=g["time"],
                points=[
                    ProfilePoint(depth_m=d, value=v)
                    for d, v in sorted(g["depths"].items(), key=lambda item: item[0])
                ],
            )
            for g in groups.values()
        ]
    finally:
        con.close()


@dataclass
class TSPair:
    depth_m: float | None
    temperature: float
    salinity: float
    lat: float | None
    lon: float | None
    time: date_type | None
    station: str | None


def get_ts_pairs(
    dataset_file: DatasetFile,
    temperature_parameter: str,
    salinity_parameter: str,
    f: RecordsFilter,
) -> list[TSPair]:
    """Paired Temperature+Salinity observations from the SAME row (same
    station+time+depth), for the T-S diagram — built via a single SQL
    self-join keyed on (station, time, depth_m) rather than two
    independent queries zipped together, so a temperature reading is
    never paired with an unrelated salinity reading that merely happens
    to share a position in two separately-fetched, separately-ordered
    lists. Depth is nullable here (unlike get_profile) — a T-S diagram is
    scientifically valid from paired T/S alone even without a depth axis;
    depth is used only for optional point coloring/tooltips when present.
    """
    con = _connect(storage_backend=dataset_file.storage_backend)
    table = _resolve_melted_table(dataset_file, con)
    try:
        _load_spatial(con)
        source_columns = _source_columns(con, table.glob)
        if temperature_parameter not in table.variable_columns or salinity_parameter not in table.variable_columns:
            return []
        melted_sql = _base_melted_sql(table)
        where_sql, params = _build_where(f, columns=_melted_columns(table, source_columns))
        has_station = "station" in source_columns
        has_time = "time" in table.coord_columns
        has_depth = "depth_m" in table.coord_columns
        # Table-qualified for a real column (t.station) but NOT for the
        # NULL literal fallback — "t.NULL" is invalid SQL (a table alias
        # can only prefix a real column reference, never a literal),
        # confirmed via direct reproduction against a station-less file.
        station_expr = "t.station" if has_station else "NULL"
        time_expr = "t.time" if has_time else "NULL"
        depth_expr = "t.depth_m" if has_depth else "NULL"

        # Join key: whichever of (station, time, depth) this file actually
        # has, joined on IS NOT DISTINCT FROM so two NULLs still match
        # (e.g. a station-less file pairs purely on time+depth) — never an
        # independent-aggregation-then-zip, per the explicit T-S pairing
        # requirement. A column absent from BOTH subqueries' SELECT * (see
        # station_expr/time_expr/depth_expr above — a coordinate the file
        # doesn't have is never materialized as a real "*"-selected column,
        # only ever referenced afterward via the outer SELECT's NULL
        # literal alias) must not be referenced inside the join condition
        # itself, or DuckDB raises "column does not exist" (confirmed via
        # direct reproduction against a real dataset with no time column).
        join_key_cols = []
        if has_station:
            join_key_cols.append("t.station IS NOT DISTINCT FROM s.station")
        if has_time:
            join_key_cols.append("t.time IS NOT DISTINCT FROM s.time")
        if has_depth:
            join_key_cols.append("t.depth_m IS NOT DISTINCT FROM s.depth_m")
        if not join_key_cols:
            # No station/time/depth at all to key on — lat/lon is the
            # only remaining shared identity a row can be paired by (same
            # fallback principle get_raw_values_with_location already
            # uses for Comparison's non-temporal pairing).
            join_key_cols = ["t.lat IS NOT DISTINCT FROM s.lat", "t.lon IS NOT DISTINCT FROM s.lon"]
        join_sql = " AND ".join(join_key_cols)

        query = (
            f"SELECT {depth_expr} AS depth_m, t.value AS temperature, s.value AS salinity, "
            f"t.lat, t.lon, {time_expr} AS time, {station_expr} AS station "
            f"FROM (SELECT * FROM {melted_sql} WHERE parameter = ? AND {where_sql}) t "
            f"JOIN (SELECT * FROM {melted_sql} WHERE parameter = ? AND {where_sql}) s "
            f"ON {join_sql}"
        )
        rows = con.execute(query, [temperature_parameter, *params, salinity_parameter, *params]).fetchall()
        return [
            TSPair(
                depth_m=float(depth_m) if depth_m is not None else None,
                temperature=float(temperature),
                salinity=float(salinity),
                lat=float(lat) if lat is not None else None,
                lon=float(lon) if lon is not None else None,
                time=(t.date() if hasattr(t, "date") else t),
                station=station,
            )
            for depth_m, temperature, salinity, lat, lon, t, station in rows
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
        where_sql, params = _build_where(f, columns=_melted_columns(table, source_columns))

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
        where_sql, params = _build_where(f, columns=_melted_columns(table, source_columns))

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
