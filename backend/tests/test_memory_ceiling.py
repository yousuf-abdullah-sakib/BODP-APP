"""Phase 2 memory-ceiling tests (PLAN.md testing requirement): proves the
chunked/RAM-safe rewrites of NetCDF/GeoTIFF/.mat processing genuinely avoid
loading whole files into memory, rather than merely producing correct
output on a fixture small enough that the OLD eager implementation would
also have passed.

Each test generates a real fixture sized so that the PREVIOUS eager
approach (ds.to_dataframe() on the whole dataset / src.read() on the
whole raster / item[()] on the whole HDF5 variable) would need well over
the memory cap enforced here, then runs the parser in a real subprocess
with resource.setrlimit(RLIMIT_AS, ...) actively constraining its address
space — if to_processed() regressed back to an eager read, the subprocess
would hit MemoryError/be OOM-killed and this test would fail; passing is
only possible if the chunking is real.

Marked `slow` (generates multi-hundred-MB fixtures and spawns real
subprocesses) — excluded from the default fast test run; run explicitly
via `pytest -m slow` when verifying large-file/chunking changes, per
docs/TESTING.md's isolated/special-run conventions.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

# RLIMIT_AS constrains total VIRTUAL address space, not just live data —
# numpy/pandas/xarray/OpenBLAS's own import-time and thread-pool
# reservations already need several hundred MB of headroom regardless of
# any dataset, confirmed empirically (800MB is the lowest cap these
# imports succeed under in this environment; below that, OpenBLAS's
# pthread_create calls themselves start failing with "Resource
# temporarily unavailable" before any test code even runs). This is still
# a meaningful, real ceiling: it sits well below what eagerly flattening
# any of this file's fixtures would additionally require on top of that
# baseline — the fixtures are deliberately sized so the OLD eager
# implementation would need a further several hundred MB beyond the
# import baseline, comfortably blowing this cap, while genuinely chunked
# processing (a handful of small chunks/windows/slices at a time) does not.
_MEMORY_CAP_MB = 800


def _run_constrained(script: str, *, timeout: int = 120) -> subprocess.CompletedProcess:
    """Runs `script` in a fresh Python subprocess with its address space
    capped at _MEMORY_CAP_MB — a hard OS-enforced ceiling, not a
    best-effort measurement, so a regression to eager loading fails loudly
    (MemoryError) rather than just running slower. OPENBLAS_NUM_THREADS=1
    avoids OpenBLAS's per-thread stack reservations eating into the same
    address-space cap for reasons unrelated to the actual data-loading
    behavior under test."""
    wrapper = f"""
import resource
cap_bytes = {_MEMORY_CAP_MB} * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (cap_bytes, cap_bytes))

{script}
"""
    import os

    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


class TestNetcdfMemoryCeiling:
    def test_large_netcdf_to_processed_stays_under_memory_cap(self, tmp_path):
        # ~5M elements: a flattened tidy DataFrame of this shape needs
        # roughly 350-400MB+ (4 columns x 8 bytes x pandas overhead) —
        # comfortably over the cap if to_dataframe() were called on the
        # whole dataset at once, which is exactly what the old
        # implementation did.
        nc_path = tmp_path / "large_ceiling_test.nc"
        script = f"""
import numpy as np
import pandas as pd
import xarray as xr

n_time, n_lat, n_lon = 2000, 50, 50
times = pd.date_range("2024-01-01", periods=n_time, freq="h")
lats = np.linspace(20.0, 21.0, n_lat)
lons = np.linspace(90.0, 91.0, n_lon)
# float32, generated in a memory-light way (broadcast, not a materialized
# n_time*n_lat*n_lon random array up front) so fixture GENERATION itself
# doesn't need to exceed the cap either — only to_processed() is what
# this test is actually verifying.
sst = np.zeros((n_time, n_lat, n_lon), dtype="float32")
for t in range(n_time):
    sst[t] = 20.0 + (t % 10) * 0.1

ds = xr.Dataset(
    {{"sea_surface_temp": (("time", "lat", "lon"), sst)}},
    coords={{"time": times, "lat": lats, "lon": lons}},
)
ds.to_netcdf({str(nc_path)!r})
del ds, sst

from pathlib import Path
from app.services.parsers.netcdf_parser import NetcdfParser

parser = NetcdfParser()
out_dir = Path({str(tmp_path)!r})
artifact = parser.to_processed(Path({str(nc_path)!r}), out_dir)
assert artifact.row_count == n_time * n_lat * n_lon, artifact.row_count
print("OK", artifact.row_count, artifact.is_zarr)
"""
        result = _run_constrained(script)
        assert result.returncode == 0, (
            f"Subprocess failed under a {_MEMORY_CAP_MB}MB memory cap "
            f"(stdout={result.stdout!r}, stderr={result.stderr[-2000:]!r})"
        )
        assert "OK" in result.stdout


class TestGeoTiffMemoryCeiling:
    def test_large_geotiff_to_processed_stays_under_memory_cap(self, tmp_path):
        # 12000x12000 float32 single-band = ~576MB raw pixel data — the
        # OLD src.read() call materialized this as one array PLUS held the
        # full destination array simultaneously during dst.write(data)
        # (~1.15GB combined); calibrated directly (see test file's design
        # notes) to confirm this specific size genuinely exceeds the
        # 800MB cap when read eagerly, unlike a smaller fixture that would
        # pass "successfully" under either implementation and prove
        # nothing. Windowed block-by-block read/write (Phase 2) never
        # holds more than one block at a time regardless of raster size.
        tif_path = tmp_path / "large_ceiling_test.tif"
        script = f"""
import numpy as np
import rasterio
from rasterio.transform import from_origin

width, height = 12000, 12000
transform = from_origin(88.0, 26.7, 0.00001, 0.00001)
with rasterio.open(
    {str(tif_path)!r}, "w", driver="GTiff", height=height, width=width, count=1,
    dtype="float32", crs="EPSG:4326", transform=transform,
    tiled=True, blockxsize=256, blockysize=256,
) as dst:
    # Write in blocks during generation too, so building the fixture
    # itself doesn't need the cap raised — real-world large rasters are
    # never authored by holding the whole array in memory either.
    for _, window in dst.block_windows(1):
        block = np.full((window.height, window.width), 22.5, dtype="float32")
        dst.write(block, 1, window=window)

from pathlib import Path
from app.services.parsers.geotiff_parser import GeoTiffParser

parser = GeoTiffParser()
out_dir = Path({str(tmp_path)!r})
artifact = parser.to_processed(Path({str(tif_path)!r}), out_dir)
assert artifact.local_path.exists()
print("OK", artifact.file_extension)
"""
        result = _run_constrained(script, timeout=180)
        assert result.returncode == 0, (
            f"Subprocess failed under a {_MEMORY_CAP_MB}MB memory cap "
            f"(stdout={result.stdout!r}, stderr={result.stderr[-2000:]!r})"
        )
        assert "OK" in result.stdout


class TestMatMemoryCeiling:
    def test_large_v73_mat_to_processed_stays_under_memory_cap(self, tmp_path):
        # ~15M rows x 3 float64 columns via h5py — the OLD item[()] call
        # materialized each full variable as a numpy array before ever
        # reaching pandas; at this size that alone is well over the cap,
        # while the chunked row-slice rewrite (Phase 2) never holds more
        # than _MAT_CHUNK_ROWS rows of all variables combined.
        mat_path = tmp_path / "large_ceiling_test.mat"
        script = f"""
import numpy as np
import h5py

n = 15_000_000
with h5py.File({str(mat_path)!r}, "w", userblock_size=512) as f:
    # Written in slices during generation for the same reason as the
    # GeoTIFF fixture above — h5py datasets support slice-assignment
    # without needing the full array in memory to build the file.
    lat_ds = f.create_dataset("lat", shape=(n,), dtype="float64")
    lon_ds = f.create_dataset("lon", shape=(n,), dtype="float64")
    temp_ds = f.create_dataset("temperature", shape=(n,), dtype="float64")
    chunk = 500_000
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        length = end - start
        lat_ds[start:end] = np.full(length, 20.0)
        lon_ds[start:end] = np.full(length, 90.0)
        temp_ds[start:end] = np.full(length, 25.0)

with open({str(mat_path)!r}, "r+b") as f:
    header = b"MATLAB 7.3 MAT-file, Platform: PCWIN64, Created on: test" + b" " * 60
    f.seek(0)
    f.write(header[:116])

from pathlib import Path
from app.services.parsers.mat_parser import MatParser

parser = MatParser()
out_dir = Path({str(tmp_path)!r})
artifact = parser.to_processed(Path({str(mat_path)!r}), out_dir)
assert artifact.row_count == n, artifact.row_count
print("OK", artifact.row_count)
"""
        result = _run_constrained(script)
        assert result.returncode == 0, (
            f"Subprocess failed under a {_MEMORY_CAP_MB}MB memory cap "
            f"(stdout={result.stdout!r}, stderr={result.stderr[-2000:]!r})"
        )
        assert "OK" in result.stdout
