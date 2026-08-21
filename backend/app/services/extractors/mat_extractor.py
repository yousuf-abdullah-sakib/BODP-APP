from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import scipy.io

from app.services.extractors.base import Extractor, ExtractionResult, ExtractorError

_LAT_NAMES = ("lat", "latitude", "y")
_LON_NAMES = ("lon", "long", "longitude", "x")
_TIME_NAMES = ("time", "date", "datetime", "t")


def _is_v73_mat(path: Path) -> bool:
    with open(path, "rb") as f:
        header = f.read(128)
    return b"MATLAB 7.3" in header


def _read_legacy(path: Path) -> dict[str, np.ndarray]:
    raw = scipy.io.loadmat(path)
    return {k: v for k, v in raw.items() if not k.startswith("__")}


def _read_v73(path: Path) -> dict[str, np.ndarray]:
    variables: dict[str, np.ndarray] = {}
    with h5py.File(path, "r") as f:
        for key in f.keys():
            if key.startswith("#"):
                continue
            item = f[key]
            if isinstance(item, h5py.Dataset):
                variables[key] = item[()]
    return variables


class MatExtractor(Extractor):
    """Filters 1-D (or squeeze-to-1-D) variables sharing the source file's
    common row length by a boolean mask, exactly mirroring MatParser's own
    "only matching-length variables are tabular" simplification at
    ingestion time (app/services/parsers/mat_parser.py) — any variable that
    doesn't share that common length (e.g. a genuinely N-D grid) is passed
    through unfiltered rather than dropped or errored, since there's no
    single row-count for it to be filtered against.

    Reads the RAW original .mat file, same reasoning and same scope-field
    coverage as NetcdfExtractor (see that class's docstring, Data Page
    Filter & Extraction Audit high #4): quality/source/platform/station/
    format/processing_level/parameters are processed-Parquet-only columns
    added by ingestion, never present in a raw file's variables — no bug,
    genuinely absent data. date_from/date_to and bounds (applied below)
    are the fields with a real analog here, matched by variable-name alias
    the same way MatParser detects them at ingestion time."""

    output_format = "mat"

    def extract(self, *, input_path: Path, output_dir: Path, scope: dict) -> ExtractionResult:
        try:
            variables = _read_v73(input_path) if _is_v73_mat(input_path) else _read_legacy(input_path)
        except Exception as exc:
            raise ExtractorError(f"Failed to read .mat file: {exc}") from exc

        if not variables:
            raise ExtractorError(".mat file contains no readable variables")

        flat = {k: np.asarray(v).ravel() for k, v in variables.items()}
        lengths = {k: v.size for k, v in flat.items()}
        common_length = max(lengths.values()) if lengths else 0
        filterable = {k: v for k, v in flat.items() if v.size == common_length}
        passthrough = {k: v for k, v in variables.items() if flat[k].size != common_length}

        mask = self._build_mask(filterable, scope)
        filtered = {k: v[mask] for k, v in filterable.items()}
        filtered.update(passthrough)

        output_path = output_dir / "extract.mat"
        try:
            scipy.io.savemat(output_path, filtered)
        except Exception as exc:
            raise ExtractorError(f"Failed to write .mat output: {exc}") from exc

        return ExtractionResult(
            local_path=output_path,
            content_type="application/octet-stream",
            file_extension="mat",
            record_count=int(mask.sum()),
        )

    def _build_mask(self, filterable: dict[str, np.ndarray], scope: dict) -> np.ndarray:
        lowered = {k.lower(): k for k in filterable}
        n = next(iter(filterable.values())).size if filterable else 0
        mask = np.ones(n, dtype=bool)

        def find(names: tuple[str, ...]) -> np.ndarray | None:
            for name in names:
                if name in lowered:
                    return filterable[lowered[name]]
            return None

        time_values = find(_TIME_NAMES)
        if time_values is not None and (scope.get("date_from") or scope.get("date_to")):
            # MATLAB datenum convention, same as MatParser. pd.to_datetime on
            # an ndarray returns a DatetimeIndex, whose comparisons already
            # yield a plain numpy bool array — no `.to_numpy()` needed (and
            # calling it on that result raises, since ndarray has no such method).
            dates = pd.to_datetime(time_values - 719529, unit="D", errors="coerce")
            if scope.get("date_from"):
                mask &= dates >= pd.Timestamp(scope["date_from"])
            if scope.get("date_to"):
                mask &= dates <= pd.Timestamp(scope["date_to"])

        bounds = scope.get("bounds")
        if bounds:
            lat_values = find(_LAT_NAMES)
            lon_values = find(_LON_NAMES)
            if lat_values is not None:
                mask &= (lat_values >= bounds["lat_min"]) & (lat_values <= bounds["lat_max"])
            if lon_values is not None:
                mask &= (lon_values >= bounds["lon_min"]) & (lon_values <= bounds["lon_max"])

        return mask
