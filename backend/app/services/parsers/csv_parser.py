from pathlib import Path

import pandas as pd

from app.services.parsers.base import (
    DataShape,
    FileParser,
    ParsedFileMetadata,
    ParserError,
    ProcessedArtifact,
)

# Column names we look for when auto-detecting spatial/temporal extent from a
# CSV — scientific data exports vary, so we check a small set of common
# aliases rather than requiring one exact schema.
_LAT_ALIASES = ("lat", "latitude", "y")
_LON_ALIASES = ("lon", "lng", "longitude", "x")
_TIME_ALIASES = ("time", "date", "datetime", "timestamp")
# Oceanographic Profiles module: vertical-coordinate detection, mirroring the
# lat/lon/time alias pattern above. "z" is deliberately excluded — unlike
# lat/lon's "x"/"y" shorthand, a bare "z" is too ambiguous outside an
# already-gridded context to safely claim as depth.
_DEPTH_ALIASES = ("depth", "depth_m", "depth_meters", "pressure_depth")


def _find_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    lowered = {c.lower(): c for c in columns}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return None


class CsvParser(FileParser):
    extensions = ("csv",)

    def parse(self, path: Path) -> ParsedFileMetadata:
        try:
            # Read only the header + a small sample first to fail fast on
            # garbage input before committing to a full read of a possibly
            # huge file.
            sample = pd.read_csv(path, nrows=5)
        except Exception as exc:
            raise ParserError(f"File is not valid CSV: {exc}") from exc

        if sample.empty and len(sample.columns) == 0:
            raise ParserError("CSV file has no columns")

        columns = list(sample.columns)
        lat_col = _find_column(columns, _LAT_ALIASES)
        lon_col = _find_column(columns, _LON_ALIASES)
        time_col = _find_column(columns, _TIME_ALIASES)
        depth_col = _find_column(columns, _DEPTH_ALIASES)

        try:
            # Now the real pass — count rows and compute extent. Chunked to
            # avoid holding an arbitrarily large CSV entirely in memory.
            row_count = 0
            lat_min = lat_max = lon_min = lon_max = None
            time_min = time_max = None
            depth_min = depth_max = None

            for chunk in pd.read_csv(path, chunksize=50_000):
                row_count += len(chunk)

                if lat_col and lat_col in chunk:
                    col = pd.to_numeric(chunk[lat_col], errors="coerce").dropna()
                    if not col.empty:
                        lat_min = col.min() if lat_min is None else min(lat_min, col.min())
                        lat_max = col.max() if lat_max is None else max(lat_max, col.max())

                if lon_col and lon_col in chunk:
                    col = pd.to_numeric(chunk[lon_col], errors="coerce").dropna()
                    if not col.empty:
                        lon_min = col.min() if lon_min is None else min(lon_min, col.min())
                        lon_max = col.max() if lon_max is None else max(lon_max, col.max())

                if time_col and time_col in chunk:
                    col = pd.to_datetime(chunk[time_col], errors="coerce").dropna()
                    if not col.empty:
                        chunk_min, chunk_max = col.min(), col.max()
                        time_min = chunk_min if time_min is None else min(time_min, chunk_min)
                        time_max = chunk_max if time_max is None else max(time_max, chunk_max)

                if depth_col and depth_col in chunk:
                    col = pd.to_numeric(chunk[depth_col], errors="coerce").dropna()
                    if not col.empty:
                        depth_min = col.min() if depth_min is None else min(depth_min, col.min())
                        depth_max = col.max() if depth_max is None else max(depth_max, col.max())
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(f"Failed to read CSV rows: {exc}") from exc

        # Positive-down is the standard oceanographic depth convention
        # (surface=0, increasing toward the seafloor). Never inferred from
        # sign alone when values could plausibly be negative-up elevation —
        # only flagged "assumed" so the API/frontend can be honest about it
        # rather than silently treating every dataset's depth_col the same
        # way regardless of what its values actually mean.
        depth_convention = None
        if depth_col is not None and depth_min is not None:
            depth_convention = "assumed_positive_down" if depth_min >= 0 else "assumed_negative_up"

        return ParsedFileMetadata(
            variables=[c for c in columns if c not in (lat_col, lon_col, time_col, depth_col)],
            dimensions={"rows": row_count},
            spatial_lat_min=float(lat_min) if lat_min is not None else None,
            spatial_lat_max=float(lat_max) if lat_max is not None else None,
            spatial_lon_min=float(lon_min) if lon_min is not None else None,
            spatial_lon_max=float(lon_max) if lon_max is not None else None,
            temporal_start=time_min.date() if time_min is not None else None,
            temporal_end=time_max.date() if time_max is not None else None,
            record_count=row_count,
            extra={
                "columns": columns,
                "lat_col": lat_col,
                "lon_col": lon_col,
                "time_col": time_col,
                "depth_col": depth_col,
                "depth_min": float(depth_min) if depth_min is not None else None,
                "depth_max": float(depth_max) if depth_max is not None else None,
                "depth_convention": depth_convention,
            },
        )

    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        output_path = output_dir / "processed.parquet"
        sample = pd.read_csv(path, nrows=5)
        time_col = _find_column(list(sample.columns), _TIME_ALIASES)
        try:
            row_count = 0
            writer = None
            for chunk in pd.read_csv(path, chunksize=100_000):
                table = _chunk_to_arrow_table(chunk, time_col=time_col)
                if writer is None:
                    import pyarrow.parquet as pq

                    writer = pq.ParquetWriter(output_path, table.schema)
                writer.write_table(table)
                row_count += len(chunk)
            if writer is not None:
                writer.close()
            else:
                # Empty file — still produce a valid (empty) parquet output.
                empty = pd.read_csv(path, nrows=0)
                empty.to_parquet(output_path, index=False)
            return ProcessedArtifact(
                local_path=output_path,
                content_type="application/vnd.apache.parquet",
                file_extension="parquet",
                row_count=row_count,
            )
        except Exception as exc:
            raise ParserError(f"Failed to convert CSV to Parquet: {exc}") from exc


def _chunk_to_arrow_table(chunk: pd.DataFrame, *, time_col: str | None):
    import pyarrow as pa

    # PLAN.md Phase 5: the Parquet output must carry a real temporal dtype
    # for its time column, not a raw string — tabular_query_service's
    # DuckDB queries (date_trunc, date-range WHERE clauses) require an
    # actual DATE/TIMESTAMP column, matching what parse()'s own metadata
    # extraction already coerces via pd.to_datetime for the SAME column.
    # Unparseable cells become NaT (skipped downstream, same as the
    # legacy per-row writer's _resolve_row_time()/_is_finite_number()
    # skip), not a hard failure — one bad row shouldn't fail the file.
    if time_col and time_col in chunk.columns:
        chunk = chunk.copy()
        chunk[time_col] = pd.to_datetime(chunk[time_col], errors="coerce")
    return pa.Table.from_pandas(chunk, preserve_index=False)
