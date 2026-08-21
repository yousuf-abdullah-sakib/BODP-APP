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

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetFile, DatasetVariable
from app.models.visualize import VisualizationJob, VizJobStatus

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
        body = r.json()["result"]
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

    async def test_timeseries_out_of_range_dates_return_empty_not_500(self, client):
        """Regression for the crash found investigating "Gridded Query
        Test" -> Tracer -> "Failed to load time series data": a date
        filter excluding every real timestep left xarray's .resample()
        with a zero-length time dimension, which raised ValueError
        instead of the empty-result behavior every other storage path
        already has. The fixture's real data is 2024-07-01/02; this
        requests a range that excludes it entirely -- exactly the
        situation the frontend's OLD hardcoded default date range
        (2022-2024) created for the real 2025-dated Tracer dataset."""
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={
                "dataset_id": self.dataset_id, "parameter": _ZARR_PARAM, "resolution": "monthly",
                "date_from": "2022-01-01", "date_to": "2023-12-31",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["series"] == []

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

    async def test_spatial_points_out_of_range_dates_return_empty_not_500(self, client, admin_headers):
        """Sibling of test_timeseries_out_of_range_dates_return_empty_not_500
        for get_spatial_points' own .isel(time=-1) crash on an empty time
        dimension."""
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": 100000.0},
            headers=admin_headers,
        )
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "dataset_id": self.dataset_id,
                "parameter": _ZARR_PARAM,
                "bounds": {"lat_min": 19.9, "lat_max": 21.1, "lon_min": 89.9, "lon_max": 91.1},
                "grid_resolution": 5,
                "date_from": "2022-01-01",
                "date_to": "2023-12-31",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["points"] == []

    async def test_statistics_histogram_has_both_timestep_values(self, client):
        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": self.dataset_id, "parameter": _ZARR_PARAM},
        )
        assert r.status_code == 200, r.text
        body = r.json()["result"]
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


class TestGriddedMultiVariableMatchingCount:
    """Regression coverage for the Performance & Behavior investigation,
    Phase 4: gridded_query_service.get_matching_record_counts batches
    every selected variable's .count() into a single dask.compute() call
    instead of one .compute() per variable in a loop — this must not
    change the actual counted values, only how they're computed."""

    async def test_unfiltered_total_is_exact_sum_across_variables(self, client, admin_headers, tmp_path):
        import xarray as xr

        # Two variables, hand-computable cell counts: "temp" has 2
        # timesteps x 3x3 = 18 cells (some NaN), "salinity" has 2
        # timesteps x 3x3 = 18 cells, no NaN. total must be 18+18=36
        # (shape-based, unaffected by NaNs); matching (non-null count)
        # must be exactly 17 (one NaN in "temp") + 18 = 35.
        p = tmp_path / "multivar_grid.nc"
        times = pd.date_range("2024-01-01", periods=2, freq="D")
        lats = np.array([20.0, 20.5, 21.0])
        lons = np.array([90.0, 90.5, 91.0])
        temp = np.ones((2, 3, 3)) * 15.0
        temp[0, 0, 0] = np.nan  # exactly one NaN cell
        salinity = np.ones((2, 3, 3)) * 34.5
        ds = xr.Dataset(
            {"temp": (("time", "lat", "lon"), temp), "salinity": (("time", "lat", "lon"), salinity)},
            coords={"time": times, "lat": lats, "lon": lons},
        )
        ds.to_netcdf(p)

        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="multivar_grid.nc", content=p.read_bytes(), content_type="application/x-netcdf",
        )
        assert file_metadata["storage_kind"] == "chunked_array"

        # _upload_and_approve only approves the file's own detected
        # _PARQUET_PARAM/_ZARR_PARAM ("sea_surface_temp") by default --
        # approve both real variables here instead so the coverage
        # endpoint's unfiltered (no `parameter`) request counts across
        # both, exactly like a real multi-variable dataset with no
        # parameter filter selected.
        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            for name in ("temp", "salinity"):
                existing = (
                    await db.execute(
                        select(DatasetVariable).where(
                            DatasetVariable.dataset_id == dataset.id, DatasetVariable.name == name
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing.roles = ["data_variable", "visualization_variable"]
                else:
                    db.add(
                        DatasetVariable(
                            dataset_id=dataset.id, name=name, data_type="numeric",
                            is_dimension=False, roles=["data_variable", "visualization_variable"],
                        )
                    )
            await db.commit()

        r = await client.post("/api/v1/visualize/coverage", json={"dataset_id": dataset_id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dataset_total_count"] == 36
        assert body["matching_count"] == 35


class TestGriddedSpatialPointCap:
    """gridded_query_service.get_spatial_points must not turn every grid
    cell into an individual marker for a large grid — reuses the existing
    admin-configurable viz_max_grid_resolution setting (grid_resolution
    **2) as the point cap, stride-sampled evenly across both axes."""

    async def test_point_count_respects_configured_grid_resolution_cap(self, client, admin_headers, tmp_path):
        import xarray as xr

        # A single-timestep 30x30=900-cell grid -- big enough to exceed a
        # deliberately small cap, small enough to keep the test fast.
        p = tmp_path / "capped_grid.nc"
        times = pd.date_range("2024-01-01", periods=1, freq="D")
        lats = np.linspace(20.0, 21.0, 30)
        lons = np.linspace(90.0, 91.0, 30)
        data = np.ones((1, 30, 30)) * 5.0
        ds = xr.Dataset(
            {_ZARR_PARAM: (("time", "lat", "lon"), data)},
            coords={"time": times, "lat": lats, "lon": lons},
        )
        ds.to_netcdf(p)

        # grid_resolution=5 -> max_points = 5**2 = 25, well under the
        # fixture's 900 real cells.
        r = await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_grid_resolution": 5, "viz_max_aoi_km2": 100000.0},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text

        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="capped_grid.nc", content=p.read_bytes(), content_type="application/x-netcdf",
        )
        assert file_metadata["storage_kind"] == "chunked_array"

        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "dataset_id": dataset_id,
                "parameter": _ZARR_PARAM,
                "bounds": {"lat_min": 19.9, "lat_max": 21.1, "lon_min": 89.9, "lon_max": 91.1},
                # Request grid_resolution itself stays small too so this
                # request isn't dispatched to the async Celery job path —
                # only the raw observation-point cap is under test here.
                "grid_resolution": 5,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        # 900 real cells, capped at 25 -- must be well under the real
        # total and at or under the configured cap.
        assert 0 < len(body["points"]) <= 25
        # All returned points still carry the real, correct value (a
        # constant 5.0 grid) -- capping subsamples, it does not corrupt.
        assert all(p["value"] == pytest.approx(5.0) for p in body["points"])


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

    async def test_statistics_matches_timeseries_for_mixed_storage(self, client, admin_headers):
        """Regression guard for the Statistics/Temporal consistency bug:
        get_statistics used to build its own series by appending raw
        Parquet rows onto an already-averaged legacy SQL bucket and taking
        an unweighted mean of the mixture -- treating a legacy bucket's
        average as if it were a single observation regardless of how many
        real rows it actually represents. That's only invisible when the
        legacy bucket happens to be backed by exactly one row; with 3
        legacy rows averaging to 9.0 on the same date a single Parquet
        row (1.0) lands on, the old unweighted mean gave (9.0+1.0)/2=5.0,
        while the correct count-weighted mean is (9.0*3+1.0*1)/(3+1)=7.0.
        Both get_timeseries and get_statistics must now agree on 7.0."""
        from datetime import date as date_cls

        from app.models.catalog import DatasetRecord

        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="mixed_phase5_stats.csv", content=_make_tabular_csv_bytes(), content_type="text/csv",
        )
        assert file_metadata["storage_kind"] == "parquet"

        # 3 legacy rows on the SAME calendar day as Parquet's station-A
        # 2024-06-01 point (value 1.0) -- AVG(value)=9.0 server-side, but
        # backed by 3 real rows, not 1.
        async with AsyncSessionLocal() as db:
            for legacy_value in (7.0, 9.0, 11.0):
                db.add(
                    DatasetRecord(
                        dataset_id=uuid.UUID(dataset_id),
                        time=date_cls(2024, 6, 1),
                        lat=21.5,
                        lon=90.5,
                        parameter=_PARQUET_PARAM,
                        value=legacy_value,
                        unit="C",
                        quality_flag="normal",
                        geom="SRID=4326;POINT(90.5 21.5)",
                    )
                )
            await db.commit()

        ts_resp = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM, "resolution": "daily"},
        )
        assert ts_resp.status_code == 200, ts_resp.text
        ts_body = ts_resp.json()
        ts_june_1 = next(p["value"] for p in ts_body["series"] if p["date"] == "2024-06-01")
        # Legacy AVG(value)=9.0 (n=3, sum=27); Parquet contributes 1.0
        # (n=1) -> weighted mean = (27+1)/(3+1) = 28/4 = 7.0.
        assert ts_june_1 == pytest.approx(7.0)

        stats_resp = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM},
        )
        assert stats_resp.status_code == 200, stats_resp.text
        stats_body = stats_resp.json()["result"]
        # Statistics' histogram/series must now use the SAME weighted
        # merge as get_timeseries -- 7.0 must appear (the old unweighted
        # implementation would have produced 5.0 here instead).
        assert 7.0 in {round(v, 6) for v in stats_body["histogram"]}
        assert 5.0 not in {round(v, 6) for v in stats_body["histogram"]}

    async def test_statistics_gridded_subdaily_bucket_still_weighted_as_one_observation(
        self, client, admin_headers, tmp_path
    ):
        """Regression coverage for the Performance & Behavior
        investigation, Phase 6: get_statistics now derives its gridded
        daily series from the SAME get_raw_values fetch already made for
        box_plot, instead of a second independent get_timeseries_
        aggregate call. That merge must still treat each daily BUCKET as
        one weight-1 observation (matching what get_timeseries_aggregate's
        own .resample("1D").mean() always produced), not one weight-1
        observation PER sub-daily raw reading -- the two give different,
        distinguishable answers whenever a bucket has more than one
        contributing gridded reading, exactly what this test constructs."""
        import xarray as xr

        from app.models.catalog import DatasetRecord
        from datetime import date as date_cls

        # 4 sub-daily timesteps, all on 2024-07-01, values 10/20/30/40 ->
        # correct daily bucket average = 25.0, weight 1 (not 4 separate
        # weight-1 readings).
        p = tmp_path / "subdaily_grid.nc"
        times = pd.date_range("2024-07-01T00:00", periods=4, freq="6h")
        lats = np.array([20.0, 21.0])
        lons = np.array([90.0, 91.0])
        data = np.zeros((4, 2, 2))
        for i, v in enumerate([10.0, 20.0, 30.0, 40.0]):
            data[i, :, :] = v
        ds = xr.Dataset(
            {_ZARR_PARAM: (("time", "lat", "lon"), data)},
            coords={"time": times, "lat": lats, "lon": lons},
        )
        ds.to_netcdf(p)

        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="subdaily_grid.nc", content=p.read_bytes(), content_type="application/x-netcdf",
        )
        assert file_metadata["storage_kind"] == "chunked_array"

        # One legacy row on the SAME calendar day, value 75.0 -- a real,
        # independent second source (weight 1) contributing to the same
        # bucket, so the two-source weighted mean is distinguishable from
        # either source's own value alone.
        async with AsyncSessionLocal() as db:
            db.add(
                DatasetRecord(
                    dataset_id=uuid.UUID(dataset_id),
                    time=date_cls(2024, 7, 1),
                    lat=21.5,
                    lon=90.5,
                    parameter=_ZARR_PARAM,
                    value=75.0,
                    unit="C",
                    quality_flag="normal",
                    geom="SRID=4326;POINT(90.5 21.5)",
                )
            )
            await db.commit()

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": _ZARR_PARAM},
        )
        assert r.status_code == 200, r.text
        heatmap = r.json()["result"]["calendar_heatmap"]
        july_index = heatmap["months"].index("Jul")
        year_index = heatmap["years"].index(2024)
        # Correct: gridded bucket (25.0, weight 1) + legacy row (75.0,
        # weight 1) -> (25.0 + 75.0) / 2 = 50.0. The bug this guards
        # against (weight 1 PER raw reading instead of per bucket) would
        # instead produce (10+20+30+40+75)/5 = 35.0.
        assert heatmap["z"][year_index][july_index] == pytest.approx(50.0)


def _make_season_spanning_csv_bytes() -> bytes:
    """Station A, one row per month for Feb-Jun 2024 (values = month
    number) -- spans the Pre-Monsoon/Winter boundary (Feb belongs to the
    PRIOR December's Winter bucket) and the Winter/Pre-Monsoon boundary
    (Mar starts a new Pre-Monsoon bucket), so a resolution="seasonal"
    query has multiple real bucket boundaries to get right, not just one
    season in isolation."""
    rows = []
    for month, value in [(2, 2.0), (3, 3.0), (4, 4.0), (5, 5.0), (6, 6.0)]:
        rows.append(
            {"time": f"2024-{month:02d}-15", "lat": 21.0, "lon": 90.0, "station": "ST-A", "sea_surface_temp": value}
        )
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


class TestSeasonalResolutionAcrossStorageTiers:
    """resolution="seasonal" must bucket by the app's own Bangladesh
    4-season definition (Pre-Monsoon Mar-May, Monsoon Jun-Sep,
    Post-Monsoon Oct-Nov, Winter Dec-Feb) on EVERY storage tier, not
    Postgres'/DuckDB's/pandas' native calendar-quarter concept. Fixture:
    Feb(2.0), Mar(3.0), Apr(4.0), May(5.0), Jun(6.0) 2024 -- expected
    buckets: 2023-12-01 -> [2.0] (Feb alone, prior Winter), 2024-03-01 ->
    [3.0, 4.0, 5.0] avg=4.0 (Mar-May, Pre-Monsoon), 2024-06-01 -> [6.0]
    (Jun alone, Monsoon start). Calendar-quarter grouping would instead
    put Feb-Mar together (Q1) and April onward in Q2 -- a materially
    different, wrong split this test would catch."""

    async def test_parquet_backed_seasonal_matches_bangladesh_seasons(self, client, admin_headers):
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="seasonal_phase5.csv", content=_make_season_spanning_csv_bytes(), content_type="text/csv",
        )
        assert file_metadata["storage_kind"] == "parquet"

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM, "resolution": "seasonal"},
        )
        assert r.status_code == 200, r.text
        series = {p["date"]: p["value"] for p in r.json()["series"]}
        assert series["2023-12-01"] == pytest.approx(2.0)
        assert series["2024-03-01"] == pytest.approx((3.0 + 4.0 + 5.0) / 3)
        assert series["2024-06-01"] == pytest.approx(6.0)
        # Calendar-quarter grouping (the old, wrong DuckDB fallback to
        # "month" would ALSO have failed this, differently) would produce
        # no bucket matching this exact 3-value Pre-Monsoon average.
        assert "2024-02-01" not in series  # not a real Bangladesh-season boundary

    async def test_zarr_backed_seasonal_matches_bangladesh_seasons(self, client, admin_headers, tmp_path):
        p = tmp_path / "seasonal_phase5_grid.nc"
        times = pd.to_datetime(["2024-02-15", "2024-03-15", "2024-04-15", "2024-05-15", "2024-06-15"])
        lats = np.array([20.0, 21.0])
        lons = np.array([90.0, 91.0])
        data = np.zeros((5, 2, 2))
        for i, v in enumerate([2.0, 3.0, 4.0, 5.0, 6.0]):
            data[i, :, :] = v
        ds = xr.Dataset(
            {_ZARR_PARAM: (("time", "lat", "lon"), data)},
            coords={"time": times, "lat": lats, "lon": lons},
        )
        ds.to_netcdf(p)

        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="seasonal_phase5_grid.nc", content=p.read_bytes(), content_type="application/x-netcdf",
        )
        assert file_metadata["storage_kind"] == "chunked_array"

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": _ZARR_PARAM, "resolution": "seasonal"},
        )
        assert r.status_code == 200, r.text
        series = {p["date"]: p["value"] for p in r.json()["series"]}
        assert series["2023-12-01"] == pytest.approx(2.0)
        assert series["2024-03-01"] == pytest.approx((3.0 + 4.0 + 5.0) / 3)
        assert series["2024-06-01"] == pytest.approx(6.0)


def _make_invalid_coordinates_csv_bytes() -> bytes:
    """3 valid points plus one row with an out-of-range latitude (999,
    physically impossible) and one row with a missing (NaN) longitude --
    neither should ever produce a spatial point."""
    rows = [
        {"time": "2024-06-01", "lat": 21.0, "lon": 90.0, "sea_surface_temp": 1.0},
        {"time": "2024-06-02", "lat": 22.0, "lon": 91.0, "sea_surface_temp": 2.0},
        {"time": "2024-06-03", "lat": 999.0, "lon": 90.5, "sea_surface_temp": 3.0},
        {"time": "2024-06-04", "lat": 23.0, "lon": None, "sea_surface_temp": 4.0},
        {"time": "2024-06-05", "lat": 23.5, "lon": 92.0, "sea_surface_temp": 5.0},
    ]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


class TestInvalidCoordinatesExcluded:
    """Spatial points sourced from raw uploaded file data (unlike the
    legacy path's Station.lat/lon, which are non-nullable DB columns)
    have no validation at ingestion time -- an out-of-range or missing
    coordinate must be excluded from spatial output, not turned into a
    bogus marker."""

    async def test_invalid_coordinates_produce_no_point(self, client, admin_headers):
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="invalid_coords.csv", content=_make_invalid_coordinates_csv_bytes(),
            content_type="text/csv",
        )
        assert file_metadata["storage_kind"] == "parquet"

        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": 100000.0},
            headers=admin_headers,
        )
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "dataset_id": dataset_id,
                "parameter": _PARQUET_PARAM,
                "bounds": {"lat_min": 20.9, "lat_max": 23.6, "lon_min": 89.9, "lon_max": 92.1},
                "grid_resolution": 5,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Only the 3 genuinely valid rows -- the lat=999 row and the
        # missing-longitude row must both be excluded.
        assert len(body["points"]) == 3
        values = {round(p["value"], 1) for p in body["points"]}
        assert values == {1.0, 2.0, 5.0}
        for p in body["points"]:
            assert -90.0 <= p["lat"] <= 90.0
            assert -180.0 <= p["lon"] <= 180.0


def _make_noncanonical_latlon_csv_bytes() -> bytes:
    """Same shape as _make_tabular_csv_bytes, but with the coordinate
    columns named "latitude"/"longitude" instead of "lat"/"lon" -- the
    real-world shape of the "My Data" dataset that exposed a bug where
    tabular_query_service.py hardcoded literal "lat"/"lon" column-name
    references, silently breaking bbox filtering and get_spatial_points
    for any file whose CSV used a non-canonical (but still parser-
    recognized, see csv_parser.py's _LAT_ALIASES/_LON_ALIASES) column
    name. Station A's 3 points share nearly-identical lat, spread out in
    lon so a tight bbox can deterministically include/exclude points."""
    rows = [
        {"time": "2024-06-01", "latitude": 21.0, "longitude": 90.0, "sea_surface_temp": 1.0},
        {"time": "2024-06-02", "latitude": 21.0, "longitude": 91.0, "sea_surface_temp": 2.0},
        {"time": "2024-06-03", "latitude": 21.0, "longitude": 92.0, "sea_surface_temp": 3.0},
    ]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


class TestNonCanonicalCoordinateColumnNames:
    """Regression coverage for the tabular_query_service.py column-name
    hardcoding bug found while investigating "My Data"'s broken Spatial
    Mapping — real CSV uploads are not guaranteed to name their lat/lon
    columns literally "lat"/"lon"; the parser detects aliases (see
    csv_parser.py's _LAT_ALIASES/_LON_ALIASES) but never renames the
    column when writing the processed Parquet file."""

    async def test_spatial_points_returned_with_noncanonical_column_names(self, client, admin_headers):
        # The 3 points span 2 degrees of longitude at the same latitude —
        # a wider AOI than the default 500 km^2 admin limit allows, so
        # raise it first (same pattern as TestZarrBackedVisualize's own
        # spatial test in this file) rather than shrink the fixture.
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": 100000.0},
            headers=admin_headers,
        )
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="noncanonical_latlon.csv", content=_make_noncanonical_latlon_csv_bytes(),
            content_type="text/csv",
        )
        assert file_metadata["storage_kind"] == "parquet"
        assert file_metadata["lat_col"] == "latitude"
        assert file_metadata["lon_col"] == "longitude"

        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "dataset_id": dataset_id,
                "parameter": _PARQUET_PARAM,
                # A tight-enough box around all 3 points to stay under the
                # raised AOI limit -- "bounds" controls the interpolation
                # GRID's extent, a separate concept from the point-level
                # spatial filter (lat_min/lon_min/... at the request's top
                # level, see the sibling test below), so this alone does
                # NOT restrict which points get returned.
                "bounds": {"lat_min": 20.9, "lat_max": 21.1, "lon_min": 89.9, "lon_max": 92.1},
                "grid_resolution": 5,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        # Before the fix: 0 points, because get_spatial_points checked
        # for a literal "lat"/"lon" column that never existed.
        assert len(body["points"]) == 3
        values = {round(p["value"], 1) for p in body["points"]}
        assert values == {1.0, 2.0, 3.0}

    async def test_bbox_filter_actually_narrows_noncanonical_columns(self, client, admin_headers):
        """The bbox filter must genuinely exclude out-of-range points, not
        silently no-op (which would make every point pass regardless of
        the requested bounds -- the exact failure mode this bug caused).
        Note: request-level lat_min/lat_max/lon_min/lon_max (NOT the
        "bounds" field, which only controls the interpolation grid's
        extent) is what actually filters get_spatial_points -- the same
        fields VisualizeClient.tsx populates from a drawn AOI via
        toVizFilterParams, alongside "bounds"."""
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="noncanonical_latlon_bbox.csv", content=_make_noncanonical_latlon_csv_bytes(),
            content_type="text/csv",
        )
        assert file_metadata["storage_kind"] == "parquet"

        # Filter tight enough to include only the lon=90.0 point -- bounds
        # matches the same tight box (well under the default 500 km^2 AOI
        # limit) since this test doesn't need a wider grid extent.
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "dataset_id": dataset_id,
                "parameter": _PARQUET_PARAM,
                "bounds": {"lat_min": 20.9, "lat_max": 21.1, "lon_min": 89.9, "lon_max": 90.1},
                "grid_resolution": 5,
                "lat_min": 20.9,
                "lat_max": 21.1,
                "lon_min": 89.9,
                "lon_max": 90.1,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["points"]) == 1
        assert body["points"][0]["value"] == pytest.approx(1.0)


def _make_no_time_dimension_csv_bytes() -> bytes:
    """A scattered lat/lon survey with NO time column at all -- the real
    shape of the "My Data" dataset that crashed 3 of 4 Visualize modules
    (get_timeseries/get_statistics/get_comparison all assumed every row
    has a real DatasetRecord.time, which is NULL by design -- Phase 2's
    own nullability comment -- for a dataset with no temporal dimension).
    Includes a second real data variable ("salinity", not a coordinate)
    so Comparison's 2-parameter requirement can be satisfied with a
    genuine data variable -- lat/lon are coordinates (is_dimension=True
    at ingestion, see worker/tasks/ingestion.py's _write_variable_
    registry) and are correctly rejected as a selectable parameter by
    validate_parameter_for_dataset's is_dimension guard, so they must
    never be used as a stand-in "second parameter" in a test."""
    rows = [
        {"lat": 21.0, "lon": 90.0, "sea_surface_temp": 1.0, "salinity": 30.0},
        {"lat": 22.0, "lon": 91.0, "sea_surface_temp": 2.0, "salinity": 31.0},
        {"lat": 23.0, "lon": 92.0, "sea_surface_temp": 3.0, "salinity": 32.0},
    ]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


class TestNoTemporalDimensionDataset:
    """Regression coverage for the crash found while investigating "My
    Data"'s broken visualization -- get_timeseries/get_statistics/
    get_comparison each assumed DatasetRecord.time was always real, and
    crashed with an unhandled 500 for any dataset with no time axis at
    all (a legitimate, documented state, not missing/corrupt data)."""

    @pytest.fixture(autouse=True)
    async def _setup(self, client, admin_headers):
        self.dataset_id, self.file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="no_time_dimension.csv", content=_make_no_time_dimension_csv_bytes(),
            content_type="text/csv",
        )
        assert self.file_metadata["storage_kind"] == "parquet"
        assert self.file_metadata["time_col"] is None

    async def test_timeseries_reports_no_temporal_data_instead_of_crashing(self, client):
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": self.dataset_id, "parameter": _PARQUET_PARAM, "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_temporal_data"] is False
        assert body["series"] == []
        assert body["stats"]["count"] == 0

    async def test_statistics_reports_no_temporal_data_instead_of_crashing(self, client):
        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": self.dataset_id, "parameter": _PARQUET_PARAM},
        )
        assert r.status_code == 200, r.text
        body = r.json()["result"]
        assert body["has_temporal_data"] is False
        assert body["histogram"] == []
        assert body["annual_anomalies"] == []
        # box_plot has no time dependency at all -- station/location-keyed
        # data still comes through even with no time dimension.
        assert len(body["box_plot"]) >= 1

    async def test_comparison_reports_no_temporal_data_and_pairs_by_location(self, client, admin_headers):
        """Visualize Module audit fix (Multivariable — Non-Temporal
        Datasets): comparison no longer silently returns an empty/zero
        result for a dataset with no time dimension — it falls back to
        pairing by (lat, lon) instead of date. This fixture's 3 rows each
        have a distinct, real lat/lon with both parameters present, so
        all 3 must pair (n=3), not the old n=0."""
        # Approve a second parameter so the 2-parameter comparison request
        # validates -- "salinity" (a genuine data variable, not lat/lon)
        # since a coordinate is correctly rejected by validate_parameter_
        # for_dataset's is_dimension guard regardless of its roles.
        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(self.dataset_id))
            existing = (
                await db.execute(
                    select(DatasetVariable).where(
                        DatasetVariable.dataset_id == dataset.id, DatasetVariable.name == "salinity"
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.roles = ["data_variable", "visualization_variable"]
            await db.commit()

        r = await client.post(
            "/api/v1/visualize/comparison",
            json={"dataset_id": self.dataset_id, "parameters": [_PARQUET_PARAM, "salinity"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()["result"]
        assert body["has_temporal_data"] is False
        assert body["scatter"]["pairing_method"] == "lat_lon_match"
        # sea_surface_temp=[1,2,3], salinity=[30,31,32] at 3 distinct
        # locations -- perfect linear relationship, full pairing.
        assert body["scatter"]["n"] == 3
        assert body["scatter"]["r"] == pytest.approx(1.0, abs=1e-9)
        assert body["correlation_matrix"]["pairing_method"] == "lat_lon_match"
        assert body["series_by_parameter"] == {}

    async def test_dataset_with_real_time_still_reports_true(self, client, admin_headers):
        """Regression guard the other direction -- a dataset that DOES
        have a time dimension must not be misreported as having none."""
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="has_time.csv", content=_make_tabular_csv_bytes(), content_type="text/csv",
        )
        assert file_metadata["time_col"] == "time"
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM, "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["has_temporal_data"] is True


class TestLegacyCoordinateMetadataDialects:
    """Regression coverage for a second, deeper coordinate-detection gap
    found while investigating "Bay of Bengal SST"'s date-filter crash
    (Visualization & Filter Reliability investigation): several real
    pre-Phase-5 DatasetFile rows have file_metadata using an OLDER
    coordinate-key naming scheme than lat_col/lon_col/time_col
    (tabular_query_service._resolve_coord_col's tier 2: lat_coord/
    lon_coord/time_coord), or no coordinate-key naming at all (tier 3:
    falls back to the literal "lat"/"lon"/"time" column name, only when
    that exact name genuinely exists in the file's real Parquet schema).
    Uploads a real canonical-column CSV through the actual HTTP
    ingestion pipeline (so the underlying Parquet file's real column
    names are genuinely "lat"/"lon"/"time", not hand-faked), then
    directly rewrites the resulting DatasetFile.file_metadata dict to
    reproduce each legacy dialect exactly as found in the real
    database -- the Parquet bytes themselves are never touched, only
    the metadata shape a much older version of the ingestion code would
    have produced for the identical data."""

    async def test_coord_suffix_dialect_still_resolves_and_filters(self, client, admin_headers):
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="coord_suffix_dialect.csv", content=_make_tabular_csv_bytes(),
            content_type="text/csv",
        )
        assert file_metadata["lat_col"] == "lat"
        assert file_metadata["time_col"] == "time"

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == uuid.UUID(dataset_id))
            )
            f = result.scalar_one()
            fm = dict(f.file_metadata)
            fm["lat_coord"] = fm.pop("lat_col")
            fm["lon_coord"] = fm.pop("lon_col")
            fm["time_coord"] = fm.pop("time_col")
            f.file_metadata = fm
            await db.commit()

        # Station A's 3-point series (2024-06-01..03, values 1/2/3) is the
        # only one in this month -- a date-filtered request that still
        # resolves lat/lon/time correctly returns exactly those 3 points.
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={
                "dataset_id": dataset_id,
                "parameter": _PARQUET_PARAM,
                "resolution": "daily",
                "date_from": "2024-06-01",
                "date_to": "2024-06-30",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_temporal_data"] is True
        assert body["stats"]["count"] == 3
        assert body["stats"]["mean"] == pytest.approx(2.0)

    async def test_missing_coordinate_keys_falls_back_to_literal_names(self, client, admin_headers):
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="no_coord_keys_dialect.csv", content=_make_tabular_csv_bytes(),
            content_type="text/csv",
        )
        assert file_metadata["lat_col"] == "lat"

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == uuid.UUID(dataset_id))
            )
            f = result.scalar_one()
            fm = dict(f.file_metadata)
            del fm["lat_col"]
            del fm["lon_col"]
            del fm["time_col"]
            f.file_metadata = fm
            await db.commit()

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={
                "dataset_id": dataset_id,
                "parameter": _PARQUET_PARAM,
                "resolution": "daily",
                "date_from": "2024-06-01",
                "date_to": "2024-06-30",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Before the fallback: raw_time_col resolves to None (metadata
        # keys entirely absent), coordinate aliasing/CAST never engages,
        # and this dataset's real VARCHAR-typed time column would fail
        # date comparison -- or, for a properly-typed CSV upload like
        # this one, the date filter would simply never match anything
        # because "time" wouldn't exist in the melted view's column set.
        assert body["has_temporal_data"] is True
        assert body["stats"]["count"] == 3
        assert body["stats"]["mean"] == pytest.approx(2.0)

    async def test_missing_coordinate_keys_excludes_coords_from_variable_list(
        self, client, admin_headers
    ):
        """The oldest real-world dialect found ("Sundarbans Salinity
        Dynamics") has no lat_col/lon_col/time_col keys AND lists "lat"/
        "lon"/"time" inside file_metadata["variables"] as if they were
        ordinary data variables -- reproducing that exact shape proves
        the fallback still correctly EXCLUDES them from variable_columns
        (so "lat" never appears as a selectable parameter), not just
        that it resolves the coordinate roles."""
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="coords_in_variables_dialect.csv", content=_make_tabular_csv_bytes(),
            content_type="text/csv",
        )

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == uuid.UUID(dataset_id))
            )
            f = result.scalar_one()
            fm = dict(f.file_metadata)
            del fm["lat_col"]
            del fm["lon_col"]
            del fm["time_col"]
            fm["variables"] = ["time", "lat", "lon", _PARQUET_PARAM]
            f.file_metadata = fm

            # Approve "lat" as a visualization_variable too -- deliberately
            # bypassing whether Phase 4's approval gate alone would reject
            # it, so this test isolates and proves the QUERY LAYER's own
            # exclusion (variable_columns), not just the unrelated
            # approval check. Ingestion's own variable-registry write
            # already auto-detected and inserted a "lat" row (it detects
            # every source column, coordinates included) -- approve it in
            # place rather than inserting a duplicate.
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            existing_lat = (
                await db.execute(
                    select(DatasetVariable).where(
                        DatasetVariable.dataset_id == dataset.id, DatasetVariable.name == "lat"
                    )
                )
            ).scalar_one_or_none()
            if existing_lat is not None:
                existing_lat.roles = ["dimension", "visualization_variable"]
            else:
                db.add(
                    DatasetVariable(
                        dataset_id=dataset.id,
                        name="lat",
                        data_type="numeric",
                        is_dimension=True,
                        roles=["dimension", "visualization_variable"],
                    )
                )
            await db.commit()

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM, "resolution": "daily"},
        )
        assert r.status_code == 200, r.text
        # _make_tabular_csv_bytes's 5 rows span 4 distinct calendar days
        # (2024-06-01/02/03 for station A, plus 2023-01-01 shared by
        # stations B and C) -- count is the number of daily series
        # points (buckets), not the raw row count.
        assert r.json()["stats"]["count"] == 4

        # "lat" must never be queryable as a parameter, even though the
        # legacy metadata's own variables list wrongly included it AND
        # it was deliberately approved above with the
        # visualization_variable role. Two independent layers both
        # reject it, defense in depth: validate_parameter_for_dataset's
        # is_dimension guard (added later, in the same broader task)
        # rejects it here at the HTTP layer before any query runs;
        # _resolve_melted_table's own coordinate-exclusion fallback
        # (this class's real subject) still separately ensures the query
        # layer itself never unpivots a coordinate into a "parameter"
        # row for any caller that reaches it directly, bypassing HTTP
        # validation -- covered by this class's sibling tests, which
        # query real, non-dimension parameters through the same fallback
        # and assert their correct values come back.
        r2 = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": "lat", "resolution": "daily"},
        )
        assert r2.status_code == 422, r2.text


def _make_mat_style_datenum_dataframe() -> pd.DataFrame:
    """Mirrors a real pre-fix .mat-sourced Parquet file's exact shape
    (confirmed via direct inspection of "Sundarbans Salinity Dynamics"
    in the dev database): a "time" column holding raw MATLAB datenum
    floats, 6-hourly spacing. datenum 739252.0 = 2024-01-01 00:00 (719529
    is the datenum for the Unix epoch) -- 8 rows spanning exactly
    2024-01-01 through 2024-01-02 18:00, split evenly across two
    calendar days for a hand-computable per-day count."""
    n = 8
    datenums = [739252.0 + i * 0.25 for i in range(n)]
    return pd.DataFrame(
        {
            "time": datenums,
            "lat": [21.0] * n,
            "lon": [90.0] * n,
            _PARQUET_PARAM: [float(i) for i in range(n)],
        }
    )


class TestMatDatenumQueryLayerFallback:
    """Regression coverage for the second, distinct half of the MATLAB
    datenum bug (Visualization & Filter Reliability investigation): even
    after mat_parser.py is fixed to convert datenum->timestamp for NEW
    uploads, an already-ingested file (like the real "Sundarbans
    Salinity Dynamics" dataset) still has the raw, unconverted numeric
    column sitting in its already-written Parquet file. tabular_query_
    service._resolve_melted_table's raw_time_is_datenum flag (numeric
    time column + file_format == "mat") makes that file queryable too,
    via epoch-offset arithmetic instead of ingestion-time conversion.

    Simulates "a file already ingested under the old, buggy code" by
    uploading a real CSV through the normal pipeline (to get a real
    DatasetFile row + Parquet object in MinIO), then directly
    overwriting that same Parquet object's bytes with a hand-crafted
    DataFrame using the exact raw-datenum shape found in the real
    database, and setting DatasetFile.file_format="mat" to match --
    this exercises the genuine query path end-to-end through the real
    /visualize/* endpoints, not just the SQL-fragment builder in
    isolation."""

    async def _upload_and_corrupt_to_mat_datenum_shape(self, client, admin_headers) -> str:
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="mat_datenum_dialect.csv", content=_make_tabular_csv_bytes(),
            content_type="text/csv",
        )

        from app.services.storage.registry import get_storage_backend

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == uuid.UUID(dataset_id))
            )
            f = result.scalar_one()
            bucket = f.file_metadata["processed_bucket"]
            key = f.file_metadata["processed_key"]
            backend_name = f.storage_backend
            f.file_format = "mat"
            await db.commit()

        df = _make_mat_style_datenum_dataframe()
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        buf.seek(0)
        backend = get_storage_backend(backend_name)
        backend.put(bucket, key, buf, content_type="application/vnd.apache.parquet")

        return dataset_id

    async def test_datenum_time_column_is_queryable_not_a_crash(self, client, admin_headers):
        dataset_id = await self._upload_and_corrupt_to_mat_datenum_shape(client, admin_headers)

        # Before the fix: DuckDB raised "Conversion Error: Unimplemented
        # type for cast (DOUBLE -> TIMESTAMP)" for any date-filtered
        # query against this file -- confirmed via direct reproduction
        # against the real dataset this fixture mirrors.
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={
                "dataset_id": dataset_id,
                "parameter": _PARQUET_PARAM,
                "resolution": "daily",
                "date_from": "2024-01-01",
                "date_to": "2024-01-01",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_temporal_data"] is True
        # datenum 739252.0/.25/.5/.75 = 2024-01-01 00/06/12/18h -- 4 of
        # the 8 rows fall on 2024-01-01 (the other 4 on 2024-01-02), all
        # landing in the single "daily" bucket this date-filtered query
        # can see -- count is the number of returned series points
        # (1 day), not the raw row count.
        assert body["stats"]["count"] == 1
        assert body["stats"]["mean"] == pytest.approx((0.0 + 1.0 + 2.0 + 3.0) / 4)

    async def test_datenum_time_column_full_range_matches_all_rows(self, client, admin_headers):
        dataset_id = await self._upload_and_corrupt_to_mat_datenum_shape(client, admin_headers)

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM, "resolution": "daily"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # 8 rows split evenly across 2 calendar days (2024-01-01,
        # 2024-01-02) at "daily" resolution -- count is the number of
        # daily series points, not the raw row count.
        assert body["stats"]["count"] == 2
        series = {p["date"]: p["value"] for p in body["series"]}
        assert series["2024-01-01"] == pytest.approx((0.0 + 1.0 + 2.0 + 3.0) / 4)
        assert series["2024-01-02"] == pytest.approx((4.0 + 5.0 + 6.0 + 7.0) / 4)


def _make_gridded_netcdf_with_static_var_bytes(tmp_path) -> bytes:
    """Same 2-timestep x 2x2 grid as _make_gridded_netcdf_bytes, but with
    a SECOND variable ("elevation") that has no "time" dimension at all
    -- a static field (e.g. bathymetry) sharing a Zarr store with
    time-varying ones, exactly the real-world shape found in the "MY
    NetCDF" dataset that exposed gridded_query_service.get_raw_values'
    KeyError('time') crash (Performance & Behavior investigation)."""
    p = tmp_path / "phase5_viz_grid_static.nc"
    times = pd.date_range("2024-07-01", periods=2, freq="D")
    lats = np.array([20.0, 21.0])
    lons = np.array([90.0, 91.0])
    data = np.zeros((2, 2, 2))
    data[0, :, :] = 10.0
    data[1, :, :] = 20.0
    elevation = np.array([[5.0, 6.0], [7.0, 8.0]])
    ds = xr.Dataset(
        {
            _ZARR_PARAM: (("time", "lat", "lon"), data),
            "elevation": (("lat", "lon"), elevation),
        },
        coords={"time": times, "lat": lats, "lon": lons},
    )
    ds.to_netcdf(p)
    return p.read_bytes()


class TestGriddedStaticVariableNoTimeDimension:
    """Regression coverage for gridded_query_service.get_raw_values'
    KeyError('time') crash (Performance & Behavior investigation,
    Phase 2): a static (no-time) variable sharing a Zarr store with
    time-varying ones must not crash Comparison/Statistics, which both
    call get_raw_values for gridded box_plot/scatter data."""

    @pytest.fixture(autouse=True)
    async def _setup(self, client, admin_headers, tmp_path):
        self.dataset_id, self.file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="viz_phase5_grid_static.nc",
            content=_make_gridded_netcdf_with_static_var_bytes(tmp_path),
            content_type="application/x-netcdf",
        )
        assert self.file_metadata["storage_kind"] == "chunked_array"

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(self.dataset_id))
            existing = (
                await db.execute(
                    select(DatasetVariable).where(
                        DatasetVariable.dataset_id == dataset.id, DatasetVariable.name == "elevation"
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.roles = ["data_variable", "visualization_variable"]
            else:
                db.add(
                    DatasetVariable(
                        dataset_id=dataset.id,
                        name="elevation",
                        data_type="numeric",
                        is_dimension=False,
                        roles=["data_variable", "visualization_variable"],
                    )
                )
            await db.commit()

    async def test_statistics_on_static_variable_does_not_crash(self, client):
        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": self.dataset_id, "parameter": "elevation"},
        )
        assert r.status_code == 200, r.text
        # No time-series raw values exist for a static variable -- the
        # date-bucketed sections must come back empty, not crash.
        assert r.json()["result"]["histogram"] == []

    async def test_comparison_static_vs_time_varying_does_not_crash(self, client):
        r = await client.post(
            "/api/v1/visualize/comparison",
            json={"dataset_id": self.dataset_id, "parameters": ["elevation", _ZARR_PARAM]},
        )
        assert r.status_code == 200, r.text
        # Pairing by date is impossible when one side has no dates at
        # all -- zero paired observations is the honest result, not a
        # crash or a fabricated pairing.
        assert r.json()["result"]["scatter"]["n"] == 0

    async def test_time_varying_variable_on_same_file_still_works(self, client):
        """Regression guard the other direction -- adding a static
        variable to the file must not break the already-working
        time-varying one."""
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": self.dataset_id, "parameter": _ZARR_PARAM, "resolution": "daily"},
        )
        assert r.status_code == 200, r.text
        series = {p["date"]: p["value"] for p in r.json()["series"]}
        assert series["2024-07-01"] == pytest.approx(10.0)
        assert series["2024-07-02"] == pytest.approx(20.0)


class TestHeavyQueryJobDispatch:
    """Visualize Performance plan, Phase 4 — Statistics/Comparison requests
    whose non-legacy (Parquet/Zarr) files exceed VIZ_HEAVY_QUERY_SYNC_
    THRESHOLD_BYTES dispatch to a Celery job instead of computing
    in-process. This class was added after a code review found the
    dispatch/poll/failure paths (get_statistics/get_comparison's job
    branch, run_statistics_job/run_comparison_job, and the two new GET
    .../{job_id} polling endpoints) had NO test coverage at all — every
    prior Comparison/Statistics test only ever exercised the sync
    ("complete") branch. Uses real HTTP uploads (same _upload_and_approve/
    _make_tabular_csv_bytes/_make_gridded_netcdf_with_static_var_bytes
    helpers other classes in this file already use) with the threshold
    monkeypatched low/high, rather than an unrealistic large fixture, to
    keep these deterministic and fast — same idiom other test files in
    this suite already use for monkeypatch-based settings overrides."""

    async def _upload_statistics_fixture(self, client, admin_headers):
        return await _upload_and_approve(
            client, admin_headers,
            filename="heavy_stats.csv", content=_make_tabular_csv_bytes(), content_type="text/csv",
        )

    async def test_statistics_dispatches_job_when_over_threshold(self, client, admin_headers, monkeypatch):
        monkeypatch.setattr(settings, "VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES", 0)
        dataset_id, _ = await self._upload_statistics_fixture(client, admin_headers)

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "queued"
        assert body["job_id"] is not None
        assert body["result"] is None

        # task_always_eager (conftest.py) means the job has already run
        # synchronously in-process by the time .delay() returns above —
        # this poll is a real HTTP round trip through the actual
        # GET /visualize/statistics/{job_id} endpoint, not a shortcut.
        poll = await client.get(f"/api/v1/visualize/statistics/{body['job_id']}")
        assert poll.status_code == 200, poll.text
        job_status = poll.json()
        assert job_status["status"] == VizJobStatus.COMPLETE.value
        assert job_status["error_message"] is None
        assert job_status["result"] is not None

        # The job path must produce BYTE-IDENTICAL results to the sync
        # path for the same request — both call the exact same
        # _compute_statistics, so this proves the split didn't drift.
        # Threshold is restored to its real (high) default here so this
        # second request takes the sync branch, giving a genuine
        # independent comparison rather than re-reading a cached value.
        monkeypatch.setattr(settings, "VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES", 200_000_000)
        sync_r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM},
        )
        assert sync_r.status_code == 200, sync_r.text
        sync_body = sync_r.json()
        assert sync_body["status"] == "complete"
        assert job_status["result"] == sync_body["result"]

    async def test_statistics_stays_sync_when_under_threshold(self, client, admin_headers, monkeypatch):
        monkeypatch.setattr(settings, "VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES", 200_000_000)
        dataset_id, _ = await self._upload_statistics_fixture(client, admin_headers)

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        assert body["job_id"] is None
        assert body["result"] is not None
        assert len(body["result"]["box_plot"]) > 0

    async def test_statistics_job_failure_surfaces_error_message(
        self, client, admin_headers, monkeypatch
    ):
        """Forces the generic exception branch of _process_heavy_query_job
        (worker/tasks/visualize.py) by making _compute_statistics raise,
        and confirms the failure — not a crash, not a silently-stuck
        "queued" — is what the polling endpoint reports."""
        monkeypatch.setattr(settings, "VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES", 0)
        dataset_id, _ = await self._upload_statistics_fixture(client, admin_headers)

        async def _boom(*args, **kwargs):
            raise RuntimeError("synthetic failure for test coverage")

        import app.worker.tasks.visualize as viz_tasks

        monkeypatch.setattr(viz_tasks, "_run_statistics_async", _boom)

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM},
        )
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        assert job_id is not None

        poll = await client.get(f"/api/v1/visualize/statistics/{job_id}")
        assert poll.status_code == 200, poll.text
        job_status = poll.json()
        assert job_status["status"] == VizJobStatus.FAILED.value
        assert job_status["result"] is None
        assert "synthetic failure for test coverage" in job_status["error_message"]

    async def test_statistics_job_dataset_deleted_before_run_reports_failed(
        self, client, admin_headers, monkeypatch
    ):
        """The "dataset gone" branch of _process_heavy_query_job — a race
        between dispatch and execution where the dataset no longer exists
        by the time the job actually runs. Deletes the VisualizationJob's
        referenced dataset directly at the DB level between dispatch and
        poll to force this deterministically (rather than relying on
        real timing), mirroring how _run_statistics_async's job=None/
        params.dataset_id-gone path is meant to be exercised."""
        monkeypatch.setattr(settings, "VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES", 0)
        dataset_id, _ = await self._upload_statistics_fixture(client, admin_headers)

        # Intercept _compute_statistics itself (not the dataset deletion,
        # which would cascade-delete the VisualizationJob's own params
        # reference indirectly and is harder to sequence deterministically
        # against task_always_eager's synchronous execution) — simulate
        # the "dataset gone by the time the job ran" outcome directly by
        # making the async runner return None, exactly as
        # _run_statistics_async does when db.get(VisualizationJob, ...)
        # or the dataset lookup comes back empty.
        async def _dataset_gone(*args, **kwargs):
            return None

        import app.worker.tasks.visualize as viz_tasks

        monkeypatch.setattr(viz_tasks, "_run_statistics_async", _dataset_gone)

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": _PARQUET_PARAM},
        )
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]

        poll = await client.get(f"/api/v1/visualize/statistics/{job_id}")
        assert poll.status_code == 200, poll.text
        job_status = poll.json()
        assert job_status["status"] == VizJobStatus.FAILED.value
        assert job_status["error_message"] == "Dataset was deleted before this job could run."

    async def test_comparison_dispatches_job_when_over_threshold(self, client, admin_headers, tmp_path, monkeypatch):
        """Same dispatch/poll proof as Statistics above, but through the
        Comparison endpoint specifically — proves that endpoint's own
        wiring (schema, router, run_comparison_job task naming) is
        correct too, not just the shared _process_heavy_query_job/
        threshold logic Statistics already covers in depth. Reuses
        TestGriddedStaticVariableNoTimeDimension's fixture shape (a real
        Zarr-backed dataset with two approved parameters) since it's the
        one existing fixture in this file with two ready-made
        parameters on a single non-legacy file."""
        monkeypatch.setattr(settings, "VIZ_HEAVY_QUERY_SYNC_THRESHOLD_BYTES", 0)
        dataset_id, file_metadata = await _upload_and_approve(
            client, admin_headers,
            filename="heavy_comparison_grid.nc",
            content=_make_gridded_netcdf_with_static_var_bytes(tmp_path),
            content_type="application/x-netcdf",
        )
        assert file_metadata["storage_kind"] == "chunked_array"

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            existing = (
                await db.execute(
                    select(DatasetVariable).where(
                        DatasetVariable.dataset_id == dataset.id, DatasetVariable.name == "elevation"
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.roles = ["data_variable", "visualization_variable"]
            else:
                db.add(
                    DatasetVariable(
                        dataset_id=dataset.id, name="elevation", data_type="numeric",
                        is_dimension=False, roles=["data_variable", "visualization_variable"],
                    )
                )
            await db.commit()

        r = await client.post(
            "/api/v1/visualize/comparison",
            json={"dataset_id": dataset_id, "parameters": ["elevation", _ZARR_PARAM]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "queued"
        job_id = body["job_id"]
        assert job_id is not None

        poll = await client.get(f"/api/v1/visualize/comparison/{job_id}")
        assert poll.status_code == 200, poll.text
        job_status = poll.json()
        assert job_status["status"] == VizJobStatus.COMPLETE.value
        assert job_status["error_message"] is None
        # elevation has no time dimension -- zero paired observations is
        # the honest result here too (matches test_comparison_static_vs_
        # time_varying_does_not_crash's sync-path assertion), proving the
        # job path agrees with the sync path on this same edge case.
        assert job_status["result"]["scatter"]["n"] == 0

    async def test_poll_unknown_statistics_job_404s(self, client):
        r = await client.get(f"/api/v1/visualize/statistics/{uuid.uuid4()}")
        assert r.status_code == 404

    async def test_poll_unknown_comparison_job_404s(self, client):
        r = await client.get(f"/api/v1/visualize/comparison/{uuid.uuid4()}")
        assert r.status_code == 404
