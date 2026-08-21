from pathlib import Path

import pandas as pd
import xarray as xr

from app.services.extractors.base import Extractor, ExtractionResult, ExtractorError

_LAT_NAMES = ("lat", "latitude", "y")
_LON_NAMES = ("lon", "long", "longitude", "x")
_TIME_NAMES = ("time", "date", "datetime")


def _find_coord(ds: xr.Dataset, names: tuple[str, ...]) -> str | None:
    lowered = {c.lower(): c for c in ds.coords}
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


class NetcdfExtractor(Extractor):
    """Reads the dataset's RAW original NetCDF (not the flattened processed
    Parquet) so N-D structure is preserved through the filter — the whole
    point of offering a NetCDF output format instead of just CSV/Parquet.

    Scope fields NOT applied here (Data Page Filter & Extraction Audit,
    high #4 — output-format consistency means every field that COULD
    apply does, not that every field is fabricated where it can't):
    quality/source/platform/station/format/processing_level are added by
    ingestion.py's _write_dataset_records when it builds the tidy
    DatasetRecord/processed-Parquet row — they never exist in the RAW
    source file this extractor deliberately reads instead (see the module
    docstring above and _source_for_format's docstring in extraction.py),
    so there is no column to filter against, for ANY source file, not
    just gridded ones. Choosing CSV/Parquet output for the same request
    applies these fields because that path reads the enriched processed
    Parquet, not because NetCDF output is missing a feature — the two
    paths read genuinely different data. date_from/date_to, bounds, and
    parameters (applied below) are universal: every real NetCDF file has
    time/lat/lon coordinates and named data variables, regardless of
    whether it went through ingestion's enrichment."""

    output_format = "netcdf"

    def extract(self, *, input_path: Path, output_dir: Path, scope: dict) -> ExtractionResult:
        try:
            with xr.open_dataset(input_path, decode_times=True) as ds:
                filtered = self._filter(ds, scope)
                output_path = output_dir / "extract.nc"
                filtered.to_netcdf(output_path)
                record_count = 1
                for v in filtered.sizes.values():
                    record_count *= max(v, 1)
        except ExtractorError:
            raise
        except Exception as exc:
            raise ExtractorError(f"Failed to subset NetCDF: {exc}") from exc

        return ExtractionResult(
            local_path=output_path,
            content_type="application/x-netcdf",
            file_extension="nc",
            record_count=record_count,
        )

    def _filter(self, ds: xr.Dataset, scope: dict) -> xr.Dataset:
        time_name = _find_coord(ds, _TIME_NAMES)
        lat_name = _find_coord(ds, _LAT_NAMES)
        lon_name = _find_coord(ds, _LON_NAMES)

        if time_name is not None and (scope.get("date_from") or scope.get("date_to")):
            lo = pd.Timestamp(scope["date_from"]) if scope.get("date_from") else ds[time_name].min().values
            hi = pd.Timestamp(scope["date_to"]) if scope.get("date_to") else ds[time_name].max().values
            ds = ds.sel({time_name: slice(lo, hi)})

        bounds = scope.get("bounds")
        if bounds:
            if lat_name is not None:
                lat_vals = ds[lat_name]
                ds = ds.where(
                    (lat_vals >= bounds["lat_min"]) & (lat_vals <= bounds["lat_max"]), drop=True
                )
            if lon_name is not None:
                lon_vals = ds[lon_name]
                ds = ds.where(
                    (lon_vals >= bounds["lon_min"]) & (lon_vals <= bounds["lon_max"]), drop=True
                )

        # Data Page Filter & Extraction Audit fix (critical #2): the
        # stored scope field is always "parameters" (plural, a list — see
        # SearchCriteriaSchema.parameters and scope_filter.apply_scope_
        # mask's identical read) — "parameter" (singular) was never a real
        # key in any stored scope, so this was a permanent no-op that
        # silently included every variable regardless of what the user
        # selected. Selects every data_var matching ANY requested
        # parameter (case-insensitive), not just the first — the tabular
        # equivalent (apply_scope_mask) is membership-match, not
        # single-value, and NetCDF's "parameters" ARE its variable names.
        parameters = scope.get("parameters")
        if parameters:
            lowered = {str(p).lower() for p in parameters}
            matching = [v for v in ds.data_vars if str(v).lower() in lowered]
            if matching:
                ds = ds[matching]

        return ds
