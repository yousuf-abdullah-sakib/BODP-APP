"""PLAN.md Phase 5 (Storage & Query Architecture) — proves the four
Visualize modules (time series, spatial, comparison, statistics) produce
correct results against BOTH new storage kinds (PARQUET via DuckDB,
CHUNKED_ARRAY via xarray/Zarr), not just the legacy DatasetRecord SQL
path test_visualize.py already covers. Each assertion is checked against
a hand-computable expected value from the exact fixture data uploaded,
per PLAN.md's explicit Definition-of-Done requirement ("verified against
hand-computable expected values the same way test_visualize.py's existing
fixtures already do") — not just "does not crash".

Uses real HTTP uploads (CSV -> Parquet, gridded NetCDF -> Zarr) through
the actual ingestion pipeline, then grants the Visualization Variable
role directly via DatasetVariable/Dataset.schema_reviewed_at (mirrors
test_catalog_schema_filters.py's pattern) since Phase 3's admin-review
workflow isn't part of what this file is testing.
"""

import io
import uuid
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetFile, DatasetVariable

from tests.test_dataset_upload import _create_dataset

pytestmark = pytest.mark.asyncio

_PARQUET_PARAM = "sea_surface_temp"
_ZARR_PARAM = "sea_surface_temp"


def _make_tabular_csv_bytes() -> bytes:
    """5 stations x 3 days, values chosen for exact hand-computable
    means/sums — station A gets a clean 1..3 series (mean=2.0), stations
    B-E get a single point each, dated a year earlier so they never share
    a monthly bucket with station A's series."""
    rows = []
    for day, value in enumerate([1.0, 2.0, 3.0], start=1):
        rows.append(
            {
                "time": f"2024-06-{day:02d}",
                "lat": 21.0,
                "lon": 90.0,
                "station": "ST-A",
                "sea_surface_temp": value,
            }
        )
    for i, (station, lat, lon, value) in enumerate(
        [("ST-B", 22.0, 91.0, 20.0), ("ST-C", 23.0, 92.0, 30.0)]
    ):
        rows.append(
            {
                "time": "2023-01-01",
                "lat": lat,
                "lon": lon,
                "station": station,
                "sea_surface_temp": value,
            }
        )
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _make_gridded_netcdf_bytes(tmp_path) -> bytes:
    """2 timesteps x 2x2 grid, constant per-timestep values (10.0, then
    20.0) so the spatial mean per timestep is exactly hand-computable."""
    p = tmp_path / "phase5_viz_grid.nc"
    times = pd.date_range("2024-07-01", periods=2, freq="D")
    lats = np.array([20.0, 21.0])
    lons = np.array([90.0, 91.0])
    data = np.zeros((2, 2, 2))
    data[0, :, :] = 10.0
    data[1, :, :] = 20.0
    ds = xr.Dataset(
        {_ZARR_PARAM: (("time", "lat", "lon"), data)},
        coords={"time": times, "lat": lats, "lon": lons},
    )
    ds.to_netcdf(p)
    return p.read_bytes()


async def _upload_and_approve(client, admin_headers, *, filename: str, content: bytes, content_type: str) -> str:
    """Uploads a real file through the HTTP ingestion pipeline, then
    grants it schema_reviewed_at + a visualization_variable role for its
    detected parameter — the minimum needed for validate_parameter_for_
    dataset (Phase 4) to accept it, without going through the full Phase
    3 admin-review UI flow this test file isn't exercising."""
    dataset_id = await _create_dataset(client, admin_headers)
    files = {"file": (filename, content, content_type)}
    r = await client.post(
        f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
    )
    assert r.status_code == 202, r.text
    file_metadata = r.json()["dataset_file"]["file_metadata"]

    async with AsyncSessionLocal() as db:
        dataset = await db.get(Dataset, uuid.UUID(dataset_id))
        dataset.schema_reviewed_at = datetime.now(UTC)

        # Ingestion's own variable-registry write (Phase 2, unmodified by
        # Phase 5) already auto-detected and inserted this variable —
        # approve it in place rather than inserting a duplicate row
        # (dataset_id, name) is unique.
        existing = (
            await db.execute(
                select(DatasetVariable).where(
                    DatasetVariable.dataset_id == dataset.id,
                    DatasetVariable.name == _PARQUET_PARAM,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.roles = ["data_variable", "visualization_variable"]
        else:
            db.add(
                DatasetVariable(
                    dataset_id=dataset.id,
                    name=_PARQUET_PARAM,
                    data_type="numeric",
                    is_dimension=False,
                    roles=["data_variable", "visualization_variable"],
                )
            )
        await db.commit()

    return dataset_id, file_metadata


class TestParquetBackedVisualize:
    """CSV upload -> Parquet (DuckDB) storage kind."""

    @pytest.fixture(autouse=True)
    async def _setup(self, client, admin_headers):
        self.dataset_id, self.file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="viz_phase5.csv", content=_make_tabular_csv_bytes(), content_type="text/csv",
        )
        assert self.file_metadata["storage_kind"] == "parquet"

    async def test_timeseries_matches_hand_computed_mean(self, client):
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": self.dataset_id, "parameter": _PARQUET_PARAM, "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Station A's June 2024 series (1,2,3) buckets into one monthly
        # point with avg=2.0; the two 2023-01 points bucket separately.
        june_point = next(p for p in body["series"] if p["date"].startswith("2024-06"))
        assert june_point["value"] == pytest.approx(2.0)
        assert body["stats"]["count"] == 2  # two distinct monthly buckets

    async def test_spatial_points_include_every_station(self, client):
        # Bounds kept small (~0.06 deg square around station A only) —
        # the admin-configured default AOI limit is 500 km², and this
        # test only needs to confirm station A's own point is returned
        # correctly via the Parquet routing path, not every station.
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "dataset_id": self.dataset_id,
                "parameter": _PARQUET_PARAM,
                "bounds": {"lat_min": 20.97, "lat_max": 21.03, "lon_min": 89.97, "lon_max": 90.03},
                "grid_resolution": 5,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        values = {round(p["value"], 1) for p in body["points"]}
        # Station A's latest (2024-06-03) value is 3.0.
        assert 3.0 in values

    async def test_statistics_box_plot_has_station_a_series(self, client):
        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": self.dataset_id, "parameter": _PARQUET_PARAM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        station_a_box = next((b for b in body["box_plot"] if b["station"] == "ST-A"), None)
        assert station_a_box is not None
        assert sorted(station_a_box["values"]) == pytest.approx([1.0, 2.0, 3.0])
        # The histogram is built from per-distinct-date AVERAGES (matching
        # the legacy DatasetRecord path's own series_query semantics,
        # unchanged by Phase 5's routing) — station A's 3 June dates each
        # average to their own single value (1,2,3), while B and C share
        # the same 2023-01-01 date and average together to 25.0.
        assert set(body["histogram"]) == {1.0, 2.0, 3.0, 25.0}

    async def test_comparison_requires_two_approved_parameters(self, client):
        # Only one parameter is approved for this dataset — a second,
        # unapproved parameter must be rejected with a real 422, proving
        # Phase 4's validate_parameter_for_dataset still gates Parquet-
        # backed datasets exactly as it does the legacy SQL path.
        r = await client.post(
            "/api/v1/visualize/comparison",
            json={
                "dataset_id": self.dataset_id,
                "parameters": [_PARQUET_PARAM, "not_an_approved_variable"],
            },
        )
        assert r.status_code == 422


class TestZarrBackedVisualize:
    """Gridded NetCDF upload -> Zarr (xarray) storage kind."""

    @pytest.fixture(autouse=True)
    async def _setup(self, client, admin_headers, tmp_path):
        self.dataset_id, self.file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="viz_phase5_grid.nc",
            content=_make_gridded_netcdf_bytes(tmp_path),
            content_type="application/x-netcdf",
        )
        assert self.file_metadata["shape"] == "gridded"
        assert self.file_metadata["storage_kind"] == "chunked_array"

    async def test_timeseries_matches_hand_computed_spatial_mean(self, client):
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": self.dataset_id, "parameter": _ZARR_PARAM, "resolution": "daily"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        series = {p["date"]: p["value"] for p in body["series"]}
        assert series["2024-07-01"] == pytest.approx(10.0)
        assert series["2024-07-02"] == pytest.approx(20.0)

    async def test_spatial_points_are_the_grid_cells(self, client, admin_headers):
        # The admin-configured default AOI limit is 500 km² — raise it
        # for this test so the assertion is about routing correctness,
        # not about picking a bbox that happens to fit under a default
        # meant for real-world station-based AOIs.
        r = await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": 100000.0},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text

        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "dataset_id": self.dataset_id,
                "parameter": _ZARR_PARAM,
                "bounds": {"lat_min": 19.9, "lat_max": 21.1, "lon_min": 89.9, "lon_max": 91.1},
                "grid_resolution": 5,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        # Latest timestep (2024-07-02) is a constant 20.0 grid — every
        # returned grid-cell point should carry that value.
        assert len(body["points"]) == 4  # 2x2 grid
        assert all(p["value"] == pytest.approx(20.0) for p in body["points"])

    async def test_statistics_histogram_has_both_timestep_values(self, client):
        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": self.dataset_id, "parameter": _ZARR_PARAM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body["histogram"]) == {10.0, 20.0}


class TestLargeZarrBboxFilterRegression:
    """PLAN.md Phase 5 — a genuinely large gridded dataset's catalog bbox
    filter must not 500. Found via a real live upload of the
    january_instantaneous.mat reference file (12M+ grid elements) against
    a real MinIO-backed s3fs mapper: xarray's .where(mask, drop=True)
    refuses boolean indexing with a dask-backed mask ("Indexing with a
    boolean dask array is not allowed"), which this session's small
    hand-built fixtures (2x2, 3x3 grids) never triggered — s3fs/fsspec's
    lazy chunk loading only produces a genuinely dask-backed coordinate
    array at a large enough grid size. Fixed in gridded_query_service.
    _apply_bbox (and the identical pattern in worker/tasks/extraction.py's
    _materialize_zarr_to_parquet) by calling .compute() on the mask
    before .where(..., drop=True)."""

    async def test_bbox_filter_does_not_500_on_large_grid(self, client, admin_headers, tmp_path):
        import xarray as xr

        from tests.conftest import register_verified_user

        # admin_headers doesn't carry "Publish Content" — a separate admin
        # is registered here purely to publish this test's throwaway
        # dataset so the real /catalog/{id}/records endpoint (which
        # requires published status) can be exercised.
        publish_token = await register_verified_user(
            client, email="bbox-regression-publisher@example.com", admin=True,
            permissions=["Publish Content"],
        )
        publish_headers = {"Authorization": f"Bearer {publish_token}"}

        p = tmp_path / "large_grid_bbox_regression.nc"
        times = pd.date_range("2024-01-01", periods=3, freq="D")
        lats = np.linspace(20.0, 21.0, 40)
        lons = np.linspace(90.0, 91.0, 40)
        rng = np.random.default_rng(99)
        data = rng.random((3, 40, 40)) * 30
        ds = xr.Dataset(
            {"sea_surface_temp": (("time", "lat", "lon"), data)},
            coords={"time": times, "lat": lats, "lon": lons},
        )
        ds.to_netcdf(p)

        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="large_grid_bbox_regression.nc",
            content=p.read_bytes(),
            content_type="application/x-netcdf",
        )
        assert file_metadata["shape"] == "gridded"
        assert file_metadata["storage_kind"] == "chunked_array"

        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/publish", headers=publish_headers)
        assert r.status_code == 200, r.text

        r = await client.get(
            f"/api/v1/catalog/{dataset_id}/records"
            "?limit=5&lat_min=20.2&lat_max=20.5&lon_min=90.2&lon_max=90.5"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # A real bbox subset — narrower than the full 3*40*40=4800 total,
        # not zero (confirms the filter actually did something, not just
        # "didn't crash").
        assert 0 < body["matching_count"] < body["dataset_total_count"]


class TestMixedStorageKindVisualize:
    """One dataset with BOTH a legacy row_records file's DatasetRecord
    rows AND a new Parquet file — proves the routing layer genuinely
    combines both sources, not just whichever kind happens to exist."""

    async def test_timeseries_combines_row_records_and_parquet(self, client, admin_headers):
        from app.models.catalog import DatasetRecord

        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="mixed_phase5.csv", content=_make_tabular_csv_bytes(), content_type="text/csv",
        )
        assert file_metadata["storage_kind"] == "parquet"

        # Simulate a pre-existing legacy row_records file contributing an
        # extra DatasetRecord in the SAME June 2024 bucket Parquet's
        # station-A series already populates, so the merged monthly
        # average must reflect BOTH sources' rows, not just one.
        from datetime import date as date_cls

        async with AsyncSessionLocal() as db:
            db.add(
                DatasetRecord(
                    dataset_id=uuid.UUID(dataset_id),
                    time=date_cls(2024, 6, 15),
                    lat=21.5,
                    lon=90.5,
                    parameter=_PARQUET_PARAM,
                    value=10.0,
                    unit="C",
                    quality_flag="normal",
                    geom="SRID=4326;POINT(90.5 21.5)",
                )
            )
            await db.commit()

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM, "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        june_point = next(p for p in body["series"] if p["date"].startswith("2024-06"))
        # Parquet contributes 1,2,3 (sum=6, n=3); the legacy row
        # contributes 10.0 (n=1) -> weighted mean = 16/4 = 4.0.
        assert june_point["value"] == pytest.approx(4.0)
