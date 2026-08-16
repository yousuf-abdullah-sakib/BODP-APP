"""Detection and tidy-row flattening for a gridded MATLAB struct packed
inside a legacy (.mat) file — e.g. hydrodynamic/environmental model output
(Delft3D, MIKE21, ROMS-style exports) saved as a 1x1 struct containing an
X/Y coordinate grid, an N-D value array, and an optional time vector.

This is additive to mat_parser.py's existing flat/tabular path: detection
only activates on a confidently-matched gridded-struct shape (see
find_gridded_struct_field below); anything else falls through to the
existing flat parsing unchanged.
"""

from dataclasses import dataclass
from itertools import permutations

import numpy as np
import pandas as pd
from pyproj import Transformer

from app.core.config import settings
from app.services.parsers.netcdf_parser import _TIME_CHUNK_SIZE, _ZARR_THRESHOLD_ELEMENTS

__all__ = [
    "GriddedStructField",
    "find_gridded_struct_field",
    "TIME_CHUNK_SIZE",
    "ZARR_THRESHOLD_ELEMENTS",
]

# Re-exported under plain names for readability at call sites in
# mat_parser.py — same values, same threshold, not a reimplementation
# (Phase 2's netcdf_parser.py already settled both numbers).
TIME_CHUNK_SIZE = _TIME_CHUNK_SIZE
ZARR_THRESHOLD_ELEMENTS = _ZARR_THRESHOLD_ELEMENTS

_COORD_NAME_HINTS = {
    "x": ("x", "lon", "longitude", "xcor", "easting"),
    "y": ("y", "lat", "latitude", "ycor", "northing"),
}
_TIME_NAME_HINTS = ("time", "t")
_VALUE_NAME_HINTS = ("val", "value", "data", "field")
_NAME_FIELD_HINTS = ("name", "variable", "parameter")
_UNITS_FIELD_HINTS = ("units", "unit")

# MATLAB datenum for real-world dates (post year ~1900) always falls in
# this range — used as a heuristic to recognize a numeric field as a
# datenum time axis rather than, say, a coordinate index.
_DATENUM_PLAUSIBLE_RANGE = (600_000, 900_000)

# A coordinate array is only treated as geographic (EPSG:4326) if EVERY
# value independently sits inside real longitude/latitude ranges — a
# projected array can coincidentally overlap [-180, 180] without actually
# being geographic (e.g. small-area local grids in meters), so range
# alone is checked per-axis, never "both happen to fit."
_LON_RANGE = (-180.0, 180.0)
_LAT_RANGE = (-90.0, 90.0)

MATLAB_DATENUM_EPOCH_OFFSET_DAYS = 719529


@dataclass
class GriddedStructField:
    """A confidently-detected gridded-struct field, already normalized to
    a consistent (time, y, x) or (y, x) layout regardless of how the
    source struct originally ordered its dimensions."""

    variable_name: str
    units: str | None
    name_is_inferred: bool
    val: np.ndarray  # (nt, ny, nx) or (ny, nx)
    x: np.ndarray  # (ny, nx)
    y: np.ndarray  # (ny, nx)
    time_datenum: np.ndarray | None  # (nt,) raw MATLAB datenum, or None for a snapshot
    is_geographic: bool


def _lowered_field_names(struct_dtype) -> dict[str, str]:
    return {n.lower(): n for n in (struct_dtype.names or ())}


def _unwrap_scalar_cell(value: np.ndarray):
    """scipy.io.loadmat wraps a struct field's contents in nested
    object/array shells (e.g. a string ends up as array(['Tracer'],
    dtype='<U6')) — this peels down to the innermost actual array."""
    v = value
    while isinstance(v, np.ndarray) and v.dtype == object and v.size == 1:
        v = v.item()
    return v


def _as_string(value) -> str | None:
    v = _unwrap_scalar_cell(value)
    if isinstance(v, np.ndarray):
        if v.size == 0:
            return None
        v = v.reshape(-1)[0]
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _find_field(lowered: dict[str, str], hints: tuple[str, ...]) -> str | None:
    for hint in hints:
        if hint in lowered:
            return lowered[hint]
    return None


def _is_geographic_pair(x: np.ndarray, y: np.ndarray) -> bool:
    """True only if BOTH arrays independently sit within real lon/lat
    ranges — never inferred from range overlap alone on just one axis."""
    x_finite = x[np.isfinite(x)]
    y_finite = y[np.isfinite(y)]
    if x_finite.size == 0 or y_finite.size == 0:
        return False
    x_ok = bool(np.min(x_finite) >= _LON_RANGE[0] and np.max(x_finite) <= _LON_RANGE[1])
    y_ok = bool(np.min(y_finite) >= _LAT_RANGE[0] and np.max(y_finite) <= _LAT_RANGE[1])
    return x_ok and y_ok


def _cell_center(grid: np.ndarray) -> np.ndarray:
    """Averages a (ny, nx) grid of node/corner coordinates down to a
    (ny-1, nx-1) grid of cell centers — the standard finite-volume/
    staggered-grid convention (Delft3D, MIKE21, and similar hydrodynamic
    model exports commonly store node corners for X/Y but values at cell
    centers, one row/column short in each spatial axis)."""
    return (grid[:-1, :-1] + grid[1:, :-1] + grid[:-1, 1:] + grid[1:, 1:]) / 4.0


def _match_value_dims(val_shape: tuple[int, ...], ny: int, nx: int, nt: int | None) -> tuple[int, ...] | None:
    """Finds which permutation of val_shape's axes corresponds to (time,
    y, x) — or (y, x) if there's no time axis — by matching sizes, not
    position. Returns the axis-order tuple to np.transpose() by (e.g.
    (1, 2, 0) if val is shaped (y, x, time)), or None if no permutation
    matches unambiguously.

    Ambiguous cases (e.g. nt == ny == nx, so even the identity mapping is
    indistinguishable from a genuine axis swap) deliberately return None
    — the caller falls back to the flat/tabular parser rather than
    guessing which axis is which.

    A square spatial grid (ny == nx, with nt distinct from both) is
    handled specially: the identity/no-transpose permutation is always
    preferred over any other equally-shape-matching permutation. This is
    NOT a coin-flip guess — X/Y coordinate grids from meshgrid/ndgrid-
    style generation (confirmed against the January instantaneous.mat
    reference dataset: X varies across columns/axis 1, Y varies across
    rows/axis 0, the standard convention) are always laid out as
    array[y_index, x_index], and a struct's own Val field is stored in
    that same field order by construction — so "don't transpose" is the
    physically correct default whenever the spatial axes happen to tie in
    size, not an arbitrary tiebreak.
    """
    target = (nt, ny, nx) if nt is not None else (ny, nx)
    if len(val_shape) != len(target):
        return None

    identity = tuple(range(len(val_shape)))
    if tuple(val_shape[p] for p in identity) == target:
        # Square-spatial-grid tie only arises when ny == nx while nt
        # (if present) differs from both — in that case multiple
        # permutations match shape-wise, but identity is preferred by
        # construction (see docstring). When nt == ny == nx too, this
        # identity match is indistinguishable from a genuine ambiguity,
        # so it still falls through to the exhaustive search below,
        # which correctly returns None for that fully-ambiguous case.
        if nt is None or (ny == nx and nt != ny):
            return identity

    matches: list[tuple[int, ...]] = []
    for perm in permutations(range(len(val_shape))):
        if tuple(val_shape[p] for p in perm) == target:
            matches.append(perm)

    if len(matches) != 1:
        return None
    return matches[0]


def find_gridded_struct_field(variables: dict[str, np.ndarray]) -> GriddedStructField | None:
    """Scans every top-level struct-typed variable for the gridded-struct
    pattern (two matching-shape 2-D coordinate arrays + a value array
    whose shape is explainable by some permutation of the coordinate/time
    dimensions). Returns the first confident match, or None if nothing in
    the file matches — callers must fall through to flat/tabular parsing
    in that case, never guess partway.
    """
    for var_name, raw in variables.items():
        arr = np.asarray(raw)
        if arr.dtype.names is None:
            continue  # not a struct

        # A 1x1 (or any-shape) struct array — MATLAB structs loaded via
        # scipy.io.loadmat always come back at least 2-D; take the first
        # element, which is where savemat puts a scalar struct's fields.
        rec = arr.reshape(-1)[0]
        lowered = _lowered_field_names(arr.dtype)

        x_key = _find_field(lowered, _COORD_NAME_HINTS["x"])
        y_key = _find_field(lowered, _COORD_NAME_HINTS["y"])
        if x_key is None or y_key is None:
            continue

        x_grid = np.asarray(_unwrap_scalar_cell(rec[x_key]), dtype=float)
        y_grid = np.asarray(_unwrap_scalar_cell(rec[y_key]), dtype=float)
        if x_grid.ndim != 2 or x_grid.shape != y_grid.shape:
            continue  # condition (a): two matching-shape 2-D coordinate arrays

        node_ny, node_nx = x_grid.shape
        center_x_grid = _cell_center(x_grid)
        center_y_grid = _cell_center(y_grid)
        center_ny, center_nx = center_x_grid.shape

        time_key = _find_field(lowered, _TIME_NAME_HINTS)
        time_datenum: np.ndarray | None = None
        nt: int | None = None
        if time_key is not None:
            time_raw = np.asarray(_unwrap_scalar_cell(rec[time_key]), dtype=float).ravel()
            if time_raw.size > 0:
                time_datenum = time_raw
                nt = time_raw.size

        value_key = _find_field(lowered, _VALUE_NAME_HINTS)
        val_candidates = [value_key] if value_key else []
        # Secondary: any remaining struct field not already claimed as a
        # coordinate/time/name/units field, in case the model export used
        # an unrecognized name for the value array itself.
        claimed = {x_key, y_key, time_key}
        val_candidates += [f for f in (arr.dtype.names or ()) if f not in claimed and f.lower() not in val_candidates]

        # Try matching against the node-corner grid first, then the
        # cell-center grid (one row/column shorter each way) — whichever
        # actually explains the value array's shape decides which
        # coordinate arrays are used for the final output.
        grid_options = [(node_ny, node_nx, x_grid, y_grid), (center_ny, center_nx, center_x_grid, center_y_grid)]

        val_grid = None
        axis_order = None
        resolved_x_grid = resolved_y_grid = None
        for candidate in val_candidates:
            if candidate is None or val_grid is not None:
                continue
            field_val = _unwrap_scalar_cell(rec[candidate])
            if isinstance(field_val, str) or (
                isinstance(field_val, np.ndarray) and field_val.dtype.kind in ("U", "S")
            ):
                continue  # a string-valued field (e.g. Name/Units) is never the value array
            try:
                field_arr = np.asarray(field_val, dtype=float)
            except (TypeError, ValueError):
                continue
            if field_arr.ndim not in (2, 3):
                continue

            for grid_ny, grid_nx, grid_x, grid_y in grid_options:
                order = _match_value_dims(field_arr.shape, grid_ny, grid_nx, nt)
                if order is not None:
                    val_grid, axis_order, value_key = field_arr, order, candidate
                    resolved_x_grid, resolved_y_grid = grid_x, grid_y
                    break
                # A value array matches the (y, x)-only shape even when a
                # time field exists elsewhere in the struct (a single
                # snapshot alongside an otherwise-unused time field) — try
                # the no-time interpretation too before giving up.
                if nt is not None:
                    order2 = _match_value_dims(field_arr.shape, grid_ny, grid_nx, None)
                    if order2 is not None:
                        val_grid, axis_order, value_key = field_arr, order2, candidate
                        resolved_x_grid, resolved_y_grid = grid_x, grid_y
                        time_datenum, nt = None, None
                        break

        if val_grid is None or axis_order is None:
            continue  # condition (b) failed — no explainable value array

        x_grid, y_grid = resolved_x_grid, resolved_y_grid
        val_normalized = np.transpose(val_grid, axis_order)

        name_field = _find_field(lowered, _NAME_FIELD_HINTS)
        units_field = _find_field(lowered, _UNITS_FIELD_HINTS)
        detected_name = _as_string(rec[name_field]) if name_field else None
        units = _as_string(rec[units_field]) if units_field else None

        variable_name = detected_name or var_name
        name_is_inferred = detected_name is None

        is_geographic = _is_geographic_pair(x_grid, y_grid)

        return GriddedStructField(
            variable_name=variable_name,
            units=units,
            name_is_inferred=name_is_inferred,
            val=val_normalized,
            x=x_grid,
            y=y_grid,
            time_datenum=time_datenum,
            is_geographic=is_geographic,
        )

    return None


def resolve_crs(field: GriddedStructField, embedded_crs: str | None) -> tuple[str | None, bool]:
    """Returns (source_crs, crs_assumed). Geographic coordinates need no
    CRS conversion at all (source_crs=None signals that to the caller).
    Projected coordinates use embedded CRS metadata if the struct
    happened to carry any, else the configured project-wide default —
    crs_assumed is True only in that last case, so stored metadata always
    records honestly whether the CRS was known or guessed."""
    if field.is_geographic:
        return None, False
    if embedded_crs:
        return embedded_crs, False
    return settings.DEFAULT_PROJECTED_CRS, True


def project_to_lonlat(x: np.ndarray, y: np.ndarray, source_crs: str) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized projected-CRS -> WGS84 transform over the whole grid at
    once (pyproj.Transformer, not a per-cell Python loop)."""
    transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(x, y)
    return np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)


def datenum_to_datetime(datenum: np.ndarray) -> pd.DatetimeIndex:
    return pd.to_datetime(datenum - MATLAB_DATENUM_EPOCH_OFFSET_DAYS, unit="D", errors="coerce")


def is_plausible_datenum(values: np.ndarray) -> bool:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return False
    lo, hi = _DATENUM_PLAUSIBLE_RANGE
    return bool(np.min(finite) >= lo and np.max(finite) <= hi)
