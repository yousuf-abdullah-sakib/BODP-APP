from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pytest
import rasterio
import scipy.io
import xarray as xr
from rasterio.transform import from_origin

from app.services.parsers import ParserError, get_parser_for_format, sniff_format
from app.services.parsers.base import DataShape


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample.csv"
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=10, freq="D"),
            "lat": [20.5 + i * 0.1 for i in range(10)],
            "lon": [90.0 + i * 0.1 for i in range(10)],
            "sea_surface_temp": [25.0 + i * 0.2 for i in range(10)],
            "salinity": [34.5 + i * 0.05 for i in range(10)],
        }
    )
    df.to_csv(p, index=False)
    return p


@pytest.fixture
def netcdf_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample.nc"
    times = pd.date_range("2024-01-01", periods=5)
    lats = np.array([20.5, 21.0])
    lons = np.array([90.0, 90.5])
    rng = np.random.default_rng(42)
    sst = rng.random((5, 2, 2)) * 30
    ds = xr.Dataset(
        {"sea_surface_temp": (("time", "lat", "lon"), sst)},
        coords={"time": times, "lat": lats, "lon": lons},
    )
    ds.attrs["title"] = "Test dataset"
    ds.to_netcdf(p)
    return p


@pytest.fixture
def netcdf_file_valid_time(tmp_path: Path) -> Path:
    """ERA5/Copernicus-style file using 'valid_time' instead of 'time' as
    its time coordinate name — matches the real Model Wave Data upload
    that revealed this gap (netcdf_parser.py's _TIME_NAMES didn't include
    it, so the file's genuine time coordinate went undetected)."""
    p = tmp_path / "sample_valid_time.nc"
    times = pd.date_range("2024-01-01", periods=5)
    lats = np.array([20.5, 21.0])
    lons = np.array([90.0, 90.5])
    rng = np.random.default_rng(7)
    wind = rng.random((5, 2, 2)) * 10
    ds = xr.Dataset(
        {"u10": (("valid_time", "lat", "lon"), wind)},
        coords={"valid_time": times, "lat": lats, "lon": lons},
    )
    ds.to_netcdf(p)
    return p


@pytest.fixture
def legacy_mat_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample_legacy.mat"
    n = 10
    scipy.io.savemat(
        p,
        {
            "lat": np.array([20.5 + i * 0.1 for i in range(n)]),
            "lon": np.array([90.0 + i * 0.1 for i in range(n)]),
            "temperature": np.array([25.0 + i * 0.2 for i in range(n)]),
        },
    )
    return p


@pytest.fixture
def v73_mat_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample_v73.mat"
    n = 10
    with h5py.File(p, "w", userblock_size=512) as f:
        f.create_dataset("lat", data=np.array([20.5 + i * 0.1 for i in range(n)]))
        f.create_dataset("lon", data=np.array([90.0 + i * 0.1 for i in range(n)]))
        f.create_dataset("temperature", data=np.array([25.0 + i * 0.2 for i in range(n)]))
    with open(p, "r+b") as f:
        header = b"MATLAB 7.3 MAT-file, Platform: PCWIN64, Created on: test" + b" " * 60
        f.seek(0)
        f.write(header[:116])
    return p


@pytest.fixture
def geotiff_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample.tif"
    width, height = 2000, 1500
    rng = np.random.default_rng(42)
    data = (rng.random((height, width)) * 30).astype("float32")
    # Bangladesh-ish bbox: lon 88.0-92.0, lat 23.7-26.7 (matches other fixtures).
    transform = from_origin(88.0, 26.7, 0.002, 0.002)
    with rasterio.open(
        p,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(data, 1)
        dst.set_band_description(1, "sea_surface_temp")
    return p


class TestCsvParser:
    def test_sniff_accepts_real_csv(self, csv_file):
        assert sniff_format(csv_file, "csv") == "csv"

    def test_parse_extracts_metadata(self, csv_file):
        parser = get_parser_for_format("csv")
        meta = parser.parse(csv_file)
        assert set(meta.variables) == {"sea_surface_temp", "salinity"}
        assert meta.record_count == 10
        assert meta.spatial_lat_min == pytest.approx(20.5)
        assert meta.spatial_lat_max == pytest.approx(21.4)
        assert meta.spatial_lon_min == pytest.approx(90.0)
        assert meta.spatial_lon_max == pytest.approx(90.9)
        assert meta.temporal_start.isoformat() == "2024-01-01"
        assert meta.temporal_end.isoformat() == "2024-01-10"

    def test_to_processed_round_trips(self, csv_file, tmp_path):
        parser = get_parser_for_format("csv")
        artifact = parser.to_processed(csv_file, tmp_path)
        assert artifact.row_count == 10
        assert artifact.file_extension == "parquet"
        assert artifact.local_path.exists()
        df = pd.read_parquet(artifact.local_path)
        assert len(df) == 10
        assert "sea_surface_temp" in df.columns

    def test_rejects_non_csv_content(self, tmp_path):
        bad = tmp_path / "fake.csv"
        bad.write_bytes(b"\x89HDF\r\n\x1a\nbinarygarbage")
        with pytest.raises(ParserError):
            sniff_format(bad, "csv")

    def test_ragged_rows_rejected_at_parse(self, tmp_path):
        """Inconsistent column counts per row pass the cheap magic-byte
        sniff (plain text, no binary signature) but must still be rejected
        at actual parse time with a clear error, not silently truncated."""
        weird = tmp_path / "weird.csv"
        weird.write_text("a,b\n1,2\n1,2,3,4,5\n")
        assert sniff_format(weird, "csv") == "csv"
        parser = get_parser_for_format("csv")
        with pytest.raises(ParserError):
            parser.parse(weird)

    def test_uniform_but_unlabeled_csv_still_parses(self, tmp_path):
        """A well-formed CSV without recognizable lat/lon/time column names
        should parse successfully — extent detection is best-effort, not a
        hard requirement for the file to be accepted."""
        p = tmp_path / "plain.csv"
        p.write_text("colA,colB\n1,2\n3,4\n5,6\n")
        parser = get_parser_for_format("csv")
        meta = parser.parse(p)
        assert meta.record_count == 3
        assert meta.spatial_lat_min is None
        assert meta.temporal_start is None


class TestNetcdfParser:
    def test_sniff_accepts_real_netcdf(self, netcdf_file):
        assert sniff_format(netcdf_file, "nc") == "nc"

    def test_parse_extracts_metadata(self, netcdf_file):
        parser = get_parser_for_format("nc")
        meta = parser.parse(netcdf_file)
        assert meta.variables == ["sea_surface_temp"]
        assert meta.dimensions == {"time": 5, "lat": 2, "lon": 2}
        assert meta.spatial_lat_min == pytest.approx(20.5)
        assert meta.spatial_lat_max == pytest.approx(21.0)
        assert meta.spatial_lon_min == pytest.approx(90.0)
        assert meta.spatial_lon_max == pytest.approx(90.5)
        assert meta.temporal_start.isoformat() == "2024-01-01"
        assert meta.temporal_end.isoformat() == "2024-01-05"
        assert meta.record_count == 20

    def test_to_processed_round_trips(self, netcdf_file, tmp_path):
        parser = get_parser_for_format("nc")
        artifact = parser.to_processed(netcdf_file, tmp_path)
        assert artifact.row_count == 20
        assert artifact.file_extension == "parquet"
        df = pd.read_parquet(artifact.local_path)
        assert "sea_surface_temp" in df.columns
        assert "lat" in df.columns and "lon" in df.columns

    def test_detects_valid_time_coordinate(self, netcdf_file_valid_time):
        """Regression test for the real Model Wave Data ERA5 file: the time
        coordinate is named 'valid_time', not 'time' — must still be
        detected so temporal extent AND the extra.time_col value (which
        ingestion._write_dataset_records relies on) are both populated."""
        parser = get_parser_for_format("nc")
        meta = parser.parse(netcdf_file_valid_time)
        assert meta.temporal_start.isoformat() == "2024-01-01"
        assert meta.temporal_end.isoformat() == "2024-01-05"
        assert meta.extra["time_col"] == "valid_time"

    def test_to_processed_spans_multiple_time_chunks(self, tmp_path):
        """Phase 2 regression test: proves the Dask/time-chunked rewrite
        produces the exact same row count/values whether a file fits in
        one chunk (_TIME_CHUNK_SIZE=24) or spans several — not just
        correct on the 5-timestep fixture, which would also pass with the
        old single ds.to_dataframe() call and wouldn't catch a chunking
        bug."""
        from app.services.parsers.netcdf_parser import _TIME_CHUNK_SIZE

        n_time = _TIME_CHUNK_SIZE * 2 + 10  # spans 3 chunks
        times = pd.date_range("2024-01-01", periods=n_time, freq="D")
        lats = np.array([20.5, 21.0])
        lons = np.array([90.0, 90.5])
        rng = np.random.default_rng(9)
        sst = rng.random((n_time, 2, 2)) * 30
        ds = xr.Dataset(
            {"sea_surface_temp": (("time", "lat", "lon"), sst)},
            coords={"time": times, "lat": lats, "lon": lons},
        )
        p = tmp_path / "multi_chunk.nc"
        ds.to_netcdf(p)

        parser = get_parser_for_format("nc")
        artifact = parser.to_processed(p, tmp_path)
        assert artifact.is_zarr is False
        assert artifact.row_count == n_time * 2 * 2

        df = pd.read_parquet(artifact.local_path)
        assert len(df) == n_time * 2 * 2
        assert df["sea_surface_temp"].min() == pytest.approx(sst.min(), rel=1e-5)
        assert df["sea_surface_temp"].max() == pytest.approx(sst.max(), rel=1e-5)

    def test_large_dataset_produces_zarr_instead_of_parquet(self, tmp_path, monkeypatch):
        """Above the size heuristic, to_processed() must switch to a
        chunked Zarr store rather than attempting a flattened Parquet
        conversion — verifies both the format switch and that the
        resulting Zarr store round-trips back to the exact same data via
        xarray, proving it's a genuinely valid, readable store and not
        just "a file got written somewhere."""
        import zarr

        from app.services.parsers.netcdf_parser import NetcdfParser
        import app.services.parsers.netcdf_parser as netcdf_module

        monkeypatch.setattr(netcdf_module, "_ZARR_THRESHOLD_ELEMENTS", 10)

        n_time = 20
        times = pd.date_range("2024-01-01", periods=n_time, freq="D")
        lats = np.linspace(20.0, 21.0, 4)
        lons = np.linspace(90.0, 91.0, 4)
        rng = np.random.default_rng(5)
        sst = rng.random((n_time, 4, 4)) * 30
        ds = xr.Dataset(
            {"sea_surface_temp": (("time", "lat", "lon"), sst)},
            coords={"time": times, "lat": lats, "lon": lons},
        )
        p = tmp_path / "large.nc"
        ds.to_netcdf(p)

        parser = NetcdfParser()
        artifact = parser.to_processed(p, tmp_path)
        assert artifact.is_zarr is True
        assert artifact.file_extension == "zarr.zip"
        assert artifact.row_count is None
        assert artifact.local_path.exists()

        store = zarr.storage.ZipStore(str(artifact.local_path), mode="r")
        reopened = xr.open_zarr(store, consolidated=False)
        assert dict(reopened.sizes) == {"time": n_time, "lat": 4, "lon": 4}
        assert np.allclose(reopened["sea_surface_temp"].values, sst, rtol=1e-5)

    def test_rejects_text_file_disguised_as_netcdf(self, tmp_path):
        fake = tmp_path / "fake.nc"
        fake.write_text("this is not a netcdf file, just plain text")
        with pytest.raises(ParserError):
            sniff_format(fake, "nc")

    def test_rejects_file_with_valid_magic_but_corrupt_body(self, tmp_path):
        bad = tmp_path / "bad_but_cdf_magic.nc"
        bad.write_bytes(b"CDF" + b"\x01" + b"garbage" * 50)
        # Passes the cheap magic-byte sniff...
        assert sniff_format(bad, "nc") == "nc"
        # ...but must still be rejected at actual parse time.
        parser = get_parser_for_format("nc")
        with pytest.raises(ParserError):
            parser.parse(bad)

    def test_rejects_netcdf_with_no_data_variables(self, tmp_path):
        p = tmp_path / "empty.nc"
        ds = xr.Dataset(coords={"lat": [1.0, 2.0]})
        ds.to_netcdf(p)
        parser = get_parser_for_format("nc")
        with pytest.raises(ParserError):
            parser.parse(p)


class TestMatParser:
    def test_sniff_accepts_legacy_mat(self, legacy_mat_file):
        assert sniff_format(legacy_mat_file, "mat") == "mat"

    def test_parse_legacy_mat(self, legacy_mat_file):
        parser = get_parser_for_format("mat")
        meta = parser.parse(legacy_mat_file)
        assert set(meta.variables) == {"lat", "lon", "temperature"}
        assert meta.spatial_lat_min == pytest.approx(20.5)
        assert meta.spatial_lat_max == pytest.approx(21.4)
        assert meta.record_count == 10

    def test_legacy_mat_to_processed(self, legacy_mat_file, tmp_path):
        parser = get_parser_for_format("mat")
        artifact = parser.to_processed(legacy_mat_file, tmp_path)
        assert artifact.row_count == 10
        assert artifact.file_extension == "parquet"
        df = pd.read_parquet(artifact.local_path)
        assert set(df.columns) == {"lat", "lon", "temperature"}

    def test_sniff_accepts_v73_mat(self, v73_mat_file):
        assert sniff_format(v73_mat_file, "mat") == "mat"

    def test_parse_v73_mat(self, v73_mat_file):
        parser = get_parser_for_format("mat")
        meta = parser.parse(v73_mat_file)
        assert set(meta.variables) == {"lat", "lon", "temperature"}
        assert meta.spatial_lat_min == pytest.approx(20.5)
        assert meta.record_count == 10

    def test_v73_mat_to_processed(self, v73_mat_file, tmp_path):
        parser = get_parser_for_format("mat")
        artifact = parser.to_processed(v73_mat_file, tmp_path)
        assert artifact.row_count == 10
        assert artifact.file_extension == "parquet"

    def test_v73_mat_to_processed_spans_multiple_chunks(self, tmp_path):
        """Phase 2 regression test: proves the chunked v7.3 rewrite
        produces the exact same row count/values whether a file fits in
        one chunk or spans several — not just correct on a tiny 10-row
        fixture, which would also pass with the old eager
        (non-chunked) implementation and wouldn't catch a chunking bug."""
        from app.services.parsers.mat_parser import _MAT_CHUNK_ROWS

        n = _MAT_CHUNK_ROWS * 2 + 500  # spans 3 chunks
        p = tmp_path / "multi_chunk_v73.mat"
        with h5py.File(p, "w", userblock_size=512) as f:
            f.create_dataset("lat", data=np.linspace(20.0, 21.0, n))
            f.create_dataset("lon", data=np.linspace(90.0, 91.0, n))
            f.create_dataset("temperature", data=np.linspace(25.0, 30.0, n))
        with open(p, "r+b") as f:
            header = b"MATLAB 7.3 MAT-file, Platform: PCWIN64, Created on: test" + b" " * 60
            f.seek(0)
            f.write(header[:116])

        parser = get_parser_for_format("mat")
        artifact = parser.to_processed(p, tmp_path)
        assert artifact.row_count == n

        df = pd.read_parquet(artifact.local_path)
        assert len(df) == n
        assert df["temperature"].min() == pytest.approx(25.0)
        assert df["temperature"].max() == pytest.approx(30.0)

    def test_rejects_legacy_mat_above_size_threshold(self, legacy_mat_file, monkeypatch):
        """A legacy (pre-v7.3) .mat file above LEGACY_MAT_MAX_SIZE_MB is
        rejected with a clear, actionable message rather than attempting
        scipy.io.loadmat's eager full-file read — that library has no
        chunked-read API, so there is no safe way to process a large
        legacy file at all; the fix is re-saving as v7.3."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "LEGACY_MAT_MAX_SIZE_MB", 0)
        parser = get_parser_for_format("mat")
        with pytest.raises(ParserError, match="exceeds the 0MB limit"):
            parser.parse(legacy_mat_file)

    def test_rejects_garbage_mat(self, tmp_path):
        bad = tmp_path / "fake.mat"
        bad.write_bytes(b"not a real mat file at all, just garbage bytes here")
        with pytest.raises(ParserError):
            sniff_format(bad, "mat")


def _save_gridded_struct_mat(
    path,
    *,
    x,
    y,
    val,
    time=None,
    field_names: dict[str, str] | None = None,
    name: str | None = "TestVariable",
    units: str | None = "unit",
    struct_var_name: str = "data",
    val_dim_order: tuple[str, ...] = ("time", "y", "x"),
):
    """Builds a synthetic legacy .mat file containing a 1x1 struct shaped
    like a real gridded model export (see january instantanteneous.mat) —
    configurable field names and value-array dimension order so the same
    helper covers every variation the gridded-struct detector must
    handle. val_dim_order describes what order `val`'s axes are ALREADY
    in (a permutation of "time"/"y"/"x", or just "y"/"x" for a snapshot)
    — the array is transposed here to match, mirroring how a real export
    might store dimensions in any order."""
    names = field_names or {}
    x_name = names.get("x", "X")
    y_name = names.get("y", "Y")
    val_name = names.get("val", "Val")
    time_name = names.get("time", "Time")
    name_field = names.get("name", "Name")
    units_field = names.get("units", "Units")

    axes = {"time": 0, "y": 1, "x": 2} if time is not None else {"y": 0, "x": 1}
    source_order = tuple(axes[d] for d in val_dim_order if d in axes)
    val_reordered = np.transpose(val, np.argsort(source_order))

    dtype_fields = [(x_name, "O"), (y_name, "O"), (val_name, "O")]
    if time is not None:
        dtype_fields.append((time_name, "O"))
    if name is not None:
        dtype_fields.append((name_field, "O"))
    if units is not None:
        dtype_fields.append((units_field, "O"))

    struct = np.zeros((1, 1), dtype=dtype_fields)
    struct[0, 0][x_name] = x
    struct[0, 0][y_name] = y
    struct[0, 0][val_name] = val_reordered
    if time is not None:
        struct[0, 0][time_name] = time.reshape(-1, 1)
    if name is not None:
        struct[0, 0][name_field] = np.array([name])
    if units is not None:
        struct[0, 0][units_field] = np.array([units])

    scipy.io.savemat(path, {struct_var_name: struct})
    return path


class TestMatGriddedStructParser:
    """Generic gridded MATLAB struct support (X/Y grid + N-D value array +
    optional time) — additive to the flat/tabular .mat path above, which
    must remain completely unaffected (see the flat-fallback regression
    test at the bottom of this class)."""

    @pytest.fixture
    def geographic_grid(self):
        lon_1d = np.linspace(90.0, 91.0, 6)
        lat_1d = np.linspace(20.0, 21.0, 5)
        x, y = np.meshgrid(lon_1d, lat_1d)  # x varies by column, y by row
        return x, y

    def test_parses_geographic_gridded_struct_with_time(self, geographic_grid, tmp_path):
        x, y = geographic_grid
        ny, nx = x.shape
        nt = 4
        rng = np.random.default_rng(1)
        val = rng.random((nt, ny, nx))
        time = 739618.0 + np.arange(nt) * 0.5

        p = tmp_path / "geo_gridded.mat"
        _save_gridded_struct_mat(p, x=x, y=y, val=val, time=time)

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)

        assert meta.variables == ["TestVariable"]
        assert meta.dimensions == {"y": ny, "x": nx, "time": nt}
        assert meta.spatial_lon_min == pytest.approx(x.min())
        assert meta.spatial_lon_max == pytest.approx(x.max())
        assert meta.spatial_lat_min == pytest.approx(y.min())
        assert meta.spatial_lat_max == pytest.approx(y.max())
        assert meta.extra["source_crs"] is None  # already geographic, no conversion
        assert meta.extra["crs_assumed"] is False
        assert meta.extra["has_time_dim"] is True
        assert meta.extra["units"] == "unit"

        artifact = parser.to_processed(p, tmp_path)
        assert artifact.row_count == nt * ny * nx
        df = pd.read_parquet(artifact.local_path)
        assert set(df.columns) == {"time", "lat", "lon", "TestVariable"}
        assert len(df) == nt * ny * nx
        # Geographic coordinates pass through unconverted.
        assert df["lon"].min() == pytest.approx(x.min())
        assert df["lat"].max() == pytest.approx(y.max())

    def test_parses_projected_gridded_struct_uses_default_crs(self, tmp_path):
        # UTM-46N-scale values (meters, not degrees) — no embedded CRS in
        # the struct, so the configured DEFAULT_PROJECTED_CRS applies and
        # crs_assumed must be True.
        x_1d = np.linspace(126768.0, 150976.0, 6)
        y_1d = np.linspace(2407270.0, 2467410.0, 5)
        x, y = np.meshgrid(x_1d, y_1d)
        ny, nx = x.shape
        val = np.zeros((ny, nx))  # single snapshot, no time

        p = tmp_path / "projected_gridded.mat"
        _save_gridded_struct_mat(p, x=x, y=y, val=val, time=None)

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)

        assert meta.extra["source_crs"] == "EPSG:32646"
        assert meta.extra["crs_assumed"] is True
        assert meta.extra["has_time_dim"] is False
        assert meta.extra["time_col"] is None
        # Converted to real Bangladesh-coastal lon/lat, not raw UTM meters.
        assert -180.0 <= meta.spatial_lon_min <= 180.0
        assert -90.0 <= meta.spatial_lat_min <= 90.0
        assert meta.spatial_lon_min == pytest.approx(89.39, abs=0.1)
        assert meta.spatial_lat_min == pytest.approx(21.7, abs=0.1)

    def test_alternate_field_names_are_detected(self, geographic_grid, tmp_path):
        """Field-name aliases (lon/lat instead of X/Y, value/time
        lowercase) must be detected the same way — detection is
        shape/dtype driven with names only as a secondary hint, never a
        hard requirement of the literal "X"/"Y"/"Val" spelling."""
        x, y = geographic_grid
        ny, nx = x.shape
        val = np.random.default_rng(2).random((ny, nx))

        p = tmp_path / "alt_names.mat"
        _save_gridded_struct_mat(
            p,
            x=x,
            y=y,
            val=val,
            time=None,
            field_names={"x": "lon", "y": "lat", "val": "value"},
            name="Wind",
            units="m/s",
        )

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)
        assert meta.variables == ["Wind"]
        assert meta.extra["units"] == "m/s"

    def test_dimension_order_yxt_is_detected(self, geographic_grid, tmp_path):
        """The value array's on-disk axis order need not be time-first —
        (y, x, time) must resolve to the same tidy output as (time, y, x)."""
        x, y = geographic_grid
        ny, nx = x.shape
        nt = 3
        rng = np.random.default_rng(3)
        val = rng.random((nt, ny, nx))

        p = tmp_path / "yxt_order.mat"
        _save_gridded_struct_mat(
            p, x=x, y=y, val=val, time=739618.0 + np.arange(nt), val_dim_order=("y", "x", "time")
        )

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)
        assert meta.dimensions == {"y": ny, "x": nx, "time": nt}

        artifact = parser.to_processed(p, tmp_path)
        assert artifact.row_count == nt * ny * nx

    def test_no_time_field_treated_as_single_snapshot(self, geographic_grid, tmp_path):
        x, y = geographic_grid
        ny, nx = x.shape
        val = np.random.default_rng(4).random((ny, nx))

        p = tmp_path / "snapshot.mat"
        _save_gridded_struct_mat(p, x=x, y=y, val=val, time=None)

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)
        assert meta.extra["has_time_dim"] is False
        assert meta.extra["time_col"] is None
        assert meta.temporal_start is None
        assert meta.temporal_end is None
        assert meta.dimensions == {"y": ny, "x": nx}

        artifact = parser.to_processed(p, tmp_path)
        assert artifact.row_count == ny * nx
        df = pd.read_parquet(artifact.local_path)
        assert "time" not in df.columns

    def test_no_name_field_falls_back_to_struct_variable_name(self, geographic_grid, tmp_path):
        x, y = geographic_grid
        ny, nx = x.shape
        val = np.random.default_rng(5).random((ny, nx))

        p = tmp_path / "no_name_field.mat"
        _save_gridded_struct_mat(p, x=x, y=y, val=val, time=None, name=None, units=None)

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)
        assert meta.variables == ["data"]  # falls back to the struct's own variable name
        assert meta.extra["variable_name_inferred"] is True
        assert meta.extra["units"] is None

    def test_square_grid_uses_identity_axis_order_not_ambiguous(self, tmp_path):
        """A square spatial grid (ny == nx) must NOT be treated as
        unresolvable ambiguity — see mat_gridded_struct._match_value_dims
        for why identity/no-transpose is the physically correct default
        here, verified against the real january instantanteneous.mat
        reference file's own meshgrid convention."""
        lon_1d = np.linspace(90.0, 91.0, 5)
        lat_1d = np.linspace(20.0, 21.0, 5)  # same length as lon -> ny == nx
        x, y = np.meshgrid(lon_1d, lat_1d)
        ny, nx = x.shape
        assert ny == nx
        nt = 3
        rng = np.random.default_rng(6)
        val = rng.random((nt, ny, nx))

        p = tmp_path / "square_grid.mat"
        _save_gridded_struct_mat(p, x=x, y=y, val=val, time=739618.0 + np.arange(nt))

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)  # must NOT fall back to flat/tabular parsing
        assert meta.variables == ["TestVariable"]
        assert meta.dimensions == {"y": ny, "x": nx, "time": nt}

    def test_cell_centered_value_grid_one_less_than_coord_grid(self, tmp_path):
        """Reproduces january instantanteneous.mat's actual shape: X/Y are
        181x181 node-corner coordinates, Val is 372x180x180 (one less per
        spatial axis) — the standard finite-volume cell-center
        convention. Detection must average corners down rather than
        rejecting the shape mismatch as unmatched."""
        lon_1d = np.linspace(89.4, 89.6, 6)
        lat_1d = np.linspace(21.7, 22.3, 6)
        x, y = np.meshgrid(lon_1d, lat_1d)  # 6x6 node grid
        nt = 3
        rng = np.random.default_rng(7)
        val = rng.random((nt, 5, 5))  # 5x5 cell-center grid (one less each way)

        p = tmp_path / "cell_centered.mat"
        _save_gridded_struct_mat(p, x=x, y=y, val=val, time=739618.0 + np.arange(nt))

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)
        assert meta.dimensions == {"y": 5, "x": 5, "time": nt}

        artifact = parser.to_processed(p, tmp_path)
        assert artifact.row_count == nt * 5 * 5

    def test_ambiguous_shape_falls_back_to_flat_tabular_parser(self, tmp_path):
        """When time length equals both spatial dimensions (nt == ny ==
        nx), even the identity permutation can't be distinguished from a
        genuine axis swap — detection must decline (return None) rather
        than guess, and parsing falls through to the flat/tabular path.
        Since a struct isn't itself flat/tabular-shaped, this ends up
        with just the struct's own top-level size as record_count,
        proving the gridded path did NOT silently produce wrong data."""
        lon_1d = np.linspace(90.0, 91.0, 4)
        lat_1d = np.linspace(20.0, 21.0, 4)
        x, y = np.meshgrid(lon_1d, lat_1d)  # 4x4 grid
        nt = 4  # deliberately == ny == nx
        rng = np.random.default_rng(8)
        val = rng.random((nt, 4, 4))

        p = tmp_path / "fully_ambiguous.mat"
        _save_gridded_struct_mat(p, x=x, y=y, val=val, time=739618.0 + np.arange(nt))

        parser = get_parser_for_format("mat")
        meta = parser.parse(p)
        # Fell through to flat/tabular parsing — did not register the
        # struct as a detected gridded variable.
        assert meta.variables == ["data"]
        assert meta.extra.get("source_crs") is None and "crs_assumed" not in meta.extra

    def test_ordinary_flat_tabular_mat_file_unaffected(self, legacy_mat_file):
        """Regression guard: an ordinary flat .mat file (lat/lon/temperature
        as top-level sibling variables, no struct at all) must parse
        exactly as before — the gridded-struct detector must not
        false-positive on it."""
        parser = get_parser_for_format("mat")
        meta = parser.parse(legacy_mat_file)
        assert set(meta.variables) == {"lat", "lon", "temperature"}
        assert "source_crs" not in meta.extra

    _REFERENCE_FILE = Path(__file__).resolve().parent / "fixtures" / "january_instantaneous.mat"

    @pytest.mark.skipif(
        not _REFERENCE_FILE.exists(), reason="real reference fixture not present in this checkout"
    )
    def test_reference_file_parses_and_ingests_without_crash(self, tmp_path):
        """Integration test against the actual file that surfaced this
        gap ('Unsupported numpy type 20' / structured-array crash) —
        confirms the fix against real (not synthetic) data."""
        parser = get_parser_for_format("mat")
        meta = parser.parse(self._REFERENCE_FILE)

        assert meta.variables == ["Tracer"]
        assert meta.dimensions == {"y": 180, "x": 180, "time": 372}
        # Bangladesh coastal (Sundarbans) bounds, not raw UTM meters.
        assert 21.0 <= meta.spatial_lat_min <= 23.0
        assert 88.0 <= meta.spatial_lon_min <= 91.0
        assert meta.extra["crs_assumed"] is True
        assert meta.extra["source_crs"] == "EPSG:32646"

        artifact = parser.to_processed(self._REFERENCE_FILE, tmp_path)
        assert artifact.row_count == 372 * 180 * 180


class TestGeoTiffParser:
    def test_sniff_accepts_real_geotiff(self, geotiff_file):
        assert sniff_format(geotiff_file, "tif") == "tif"

    def test_parse_extracts_raster_metadata(self, geotiff_file):
        parser = get_parser_for_format("tif")
        meta = parser.parse(geotiff_file)
        assert meta.shape == DataShape.RASTER
        assert meta.bands == ["sea_surface_temp"]
        assert meta.pixel_width == 2000
        assert meta.pixel_height == 1500
        assert meta.spatial_lon_min == pytest.approx(88.0)
        assert meta.spatial_lon_max == pytest.approx(92.0)
        assert meta.spatial_lat_min == pytest.approx(23.7)
        assert meta.spatial_lat_max == pytest.approx(26.7)
        # Raster files don't have tabular variables/record_count.
        assert meta.variables == []
        assert meta.record_count is None

    def test_to_processed_produces_valid_cog(self, geotiff_file, tmp_path):
        parser = get_parser_for_format("tif")
        artifact = parser.to_processed(geotiff_file, tmp_path)
        assert artifact.file_extension == "cog.tif"
        assert artifact.row_count is None
        assert artifact.local_path.exists()

        with rasterio.open(artifact.local_path) as ds:
            assert ds.overviews(1) == [2, 4]
            assert ds.profile.get("tiled") is True
            assert ds.crs.to_string() == "EPSG:4326"
            assert ds.bounds.left == pytest.approx(88.0)
            assert ds.bounds.top == pytest.approx(26.7)

    def test_band_description_preserved_in_cog(self, geotiff_file, tmp_path):
        parser = get_parser_for_format("tif")
        artifact = parser.to_processed(geotiff_file, tmp_path)
        with rasterio.open(artifact.local_path) as ds:
            assert ds.descriptions[0] == "sea_surface_temp"

    def test_rejects_garbage_geotiff(self, tmp_path):
        bad = tmp_path / "fake.tif"
        bad.write_bytes(b"not a real tiff file, just plain garbage bytes")
        with pytest.raises(ParserError):
            sniff_format(bad, "tif")

    def test_rejects_geotiff_without_crs(self, tmp_path):
        p = tmp_path / "no_crs.tif"
        data = np.zeros((10, 10), dtype="uint8")
        with rasterio.open(p, "w", driver="GTiff", height=10, width=10, count=1, dtype="uint8") as dst:
            dst.write(data, 1)
        # No CRS is still a structurally valid TIFF, so it passes the magic-byte
        # sniff — the rejection must happen at actual parse time.
        assert sniff_format(p, "tif") == "tif"
        parser = get_parser_for_format("tif")
        with pytest.raises(ParserError, match="coordinate reference system"):
            parser.parse(p)

    def test_alternate_extensions_resolve_to_same_parser(self, geotiff_file):
        for ext in ("tif", "tiff", "geotiff"):
            parser = get_parser_for_format(ext)
            meta = parser.parse(geotiff_file)
            assert meta.shape == DataShape.RASTER


class TestRegistry:
    def test_unsupported_format_raises(self):
        with pytest.raises(ParserError):
            get_parser_for_format("zarr")

    def test_unsupported_extension_sniff_raises(self, tmp_path):
        p = tmp_path / "sample.xyz"
        p.write_bytes(b"whatever")
        with pytest.raises(ParserError):
            sniff_format(p, "xyz")

    def test_geotiff_is_registered(self):
        from app.services.parsers.geotiff_parser import GeoTiffParser

        for ext in ("tif", "tiff", "geotiff"):
            assert isinstance(get_parser_for_format(ext), GeoTiffParser)
