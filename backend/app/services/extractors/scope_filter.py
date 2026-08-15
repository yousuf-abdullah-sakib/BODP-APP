import pandas as pd

# Same alias sets the ingestion parsers use to auto-detect spatial/temporal
# columns (app/services/parsers/csv_parser.py) — reused here so extraction
# filters against whichever column name the source file actually has.
_LAT_ALIASES = ("lat", "latitude", "y")
_LON_ALIASES = ("lon", "lng", "longitude", "x")
_TIME_ALIASES = ("time", "date", "datetime", "timestamp")


def _find_column(columns, aliases: tuple[str, ...]) -> str | None:
    lowered = {c.lower(): c for c in columns}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return None


def apply_scope_mask(df: pd.DataFrame, scope: dict) -> pd.DataFrame:
    """Filters a tidy/long-format DataFrame by a SearchCriteriaSchema-shaped
    scope dict — date range on the time column, bbox on lat/lon columns,
    exact-match on the source column, membership-match on parameters
    (checkbox multi-select — zero/empty means no parameter filtering)
    wherever both the scope field and a matching column are present. Any
    scope field with no matching column in this particular file is
    silently skipped rather than erroring — not every dataset's file has
    every column (e.g. a single-parameter dataset has no "parameter"
    column to filter on)."""
    mask = pd.Series(True, index=df.index)
    columns = list(df.columns)

    time_col = _find_column(columns, _TIME_ALIASES)
    if time_col is not None:
        times = pd.to_datetime(df[time_col], errors="coerce")
        if scope.get("date_from"):
            mask &= times >= pd.Timestamp(scope["date_from"])
        if scope.get("date_to"):
            mask &= times <= pd.Timestamp(scope["date_to"])

    bounds = scope.get("bounds")
    if bounds:
        lat_col = _find_column(columns, _LAT_ALIASES)
        lon_col = _find_column(columns, _LON_ALIASES)
        if lat_col is not None:
            lats = pd.to_numeric(df[lat_col], errors="coerce")
            mask &= (lats >= bounds["lat_min"]) & (lats <= bounds["lat_max"])
        if lon_col is not None:
            lons = pd.to_numeric(df[lon_col], errors="coerce")
            mask &= (lons >= bounds["lon_min"]) & (lons <= bounds["lon_max"])

    source_value = scope.get("source")
    if source_value:
        col = _find_column(columns, ("source",))
        if col is not None:
            mask &= df[col].astype(str).str.lower() == str(source_value).lower()

    parameters = scope.get("parameters")
    if parameters:
        col = _find_column(columns, ("parameter", "variable"))
        if col is not None:
            lowered_values = {str(p).lower() for p in parameters}
            mask &= df[col].astype(str).str.lower().isin(lowered_values)

    return df[mask]
