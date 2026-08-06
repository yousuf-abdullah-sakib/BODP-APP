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
    point of offering a NetCDF output format instead of just CSV/Parquet."""

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

        parameter = scope.get("parameter")
        if parameter:
            matching = [v for v in ds.data_vars if v.lower() == str(parameter).lower()]
            if matching:
                ds = ds[matching]

        return ds
