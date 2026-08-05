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

        try:
            # Now the real pass — count rows and compute extent. Chunked to
            # avoid holding an arbitrarily large CSV entirely in memory.
            row_count = 0
            lat_min = lat_max = lon_min = lon_max = None
            time_min = time_max = None

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
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(f"Failed to read CSV rows: {exc}") from exc

        return ParsedFileMetadata(
            variables=[c for c in columns if c not in (lat_col, lon_col, time_col)],
            dimensions={"rows": row_count},
            spatial_lat_min=float(lat_min) if lat_min is not None else None,
            spatial_lat_max=float(lat_max) if lat_max is not None else None,
            spatial_lon_min=float(lon_min) if lon_min is not None else None,
            spatial_lon_max=float(lon_max) if lon_max is not None else None,
            temporal_start=time_min.date() if time_min is not None else None,
            temporal_end=time_max.date() if time_max is not None else None,
            record_count=row_count,
            extra={"columns": columns},
        )

    def to_processed(self, path: Path, output_dir: Path) -> ProcessedArtifact:
        output_path = output_dir / "processed.parquet"
        try:
            row_count = 0
            writer = None
            for chunk in pd.read_csv(path, chunksize=100_000):
                table = _chunk_to_arrow_table(chunk)
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


def _chunk_to_arrow_table(chunk: pd.DataFrame):
    import pyarrow as pa

    return pa.Table.from_pandas(chunk, preserve_index=False)
