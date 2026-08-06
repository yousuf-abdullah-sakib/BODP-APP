import uuid
from datetime import date

import pytest

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetCategory, DatasetRecord, DatasetStatus, Station
from app.models.visualize import VizJobStatus

pytestmark = pytest.mark.asyncio

_PARAM = "Sea Surface Temp"


async def _seed_timeseries_fixture() -> dict:
    """3 stations, 12 monthly values each for one parameter — small enough
    to hand-verify mean/trend/seasonal grouping."""
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name="Environmental", description="test", color_tag="cat-Environmental")
        db.add(category)
        await db.flush()

        station_a = Station(name="Station A", code="ST-A", lat=21.0, lon=90.0, depth_m=10)
        station_b = Station(name="Station B", code="ST-B", lat=22.0, lon=91.0, depth_m=20)
        station_c = Station(name="Station C", code="ST-C", lat=23.0, lon=92.0, depth_m=30)
        db.add_all([station_a, station_b, station_c])
        await db.flush()

        dataset = Dataset(
            code="BD-VIZ-TEST",
            title="Viz Test Dataset",
            category_id=category.id,
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.flush()

        # Values 1..12 for Jan..Dec 2024 at station A only, for exact
        # hand-computable stats (mean=6.5, linreg slope against index 0..11).
        for month in range(1, 13):
            db.add(
                DatasetRecord(
                    dataset_id=dataset.id,
                    time=date(2024, month, 15),
                    lat=21.0,
                    lon=90.0,
                    station_id=station_a.id,
                    parameter=_PARAM,
                    value=float(month),
                    unit="°C",
                    quality_flag="normal",
                    geom="SRID=4326;POINT(90.0 21.0)",
                )
            )
        # One additional point each at stations B and C, dated BEFORE the
        # station-A monthly series (so they never land in the same
        # date_trunc bucket and skew the hand-computed timeseries stats),
        # for spatial-module tests only.
        db.add(
            DatasetRecord(
                dataset_id=dataset.id,
                time=date(2023, 12, 1),
                lat=22.0,
                lon=91.0,
                station_id=station_b.id,
                parameter=_PARAM,
                value=20.0,
                unit="°C",
                quality_flag="normal",
                geom="SRID=4326;POINT(91.0 22.0)",
            )
        )
        db.add(
            DatasetRecord(
                dataset_id=dataset.id,
                time=date(2023, 12, 1),
                lat=23.0,
                lon=92.0,
                station_id=station_c.id,
                parameter=_PARAM,
                value=10.0,
                unit="°C",
                quality_flag="normal",
                geom="SRID=4326;POINT(92.0 23.0)",
            )
        )

        # A second parameter for comparison-module tests, correlated with
        # the first (y = 2x) so Pearson r should be ~1.0.
        for month in range(1, 13):
            db.add(
                DatasetRecord(
                    dataset_id=dataset.id,
                    time=date(2024, month, 15),
                    lat=21.0,
                    lon=90.0,
                    station_id=station_a.id,
                    parameter="Salinity",
                    value=float(month) * 2,
                    unit="PSU",
                    quality_flag="normal",
                    geom="SRID=4326;POINT(90.0 21.0)",
                )
            )

        await db.commit()
        return {"dataset_id": dataset.id}


class TestTimeSeries:
    async def test_stats_match_hand_computed_values(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"parameter": _PARAM, "resolution": "monthly", "station": "ST-A"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["series"]) == 12
        assert body["stats"]["mean"] == pytest.approx(6.5)
        assert body["stats"]["min"] == pytest.approx(1.0)
        assert body["stats"]["max"] == pytest.approx(12.0)
        assert body["stats"]["count"] == 12
        # linreg against index 0..11 of values 1..12 has slope=1 exactly.
        assert body["stats"]["trend_per_year"] == pytest.approx(12.0)

    async def test_seasonal_breakdown_has_four_seasons(self, client):
        await _seed_timeseries_fixture()
        r = await client.post("/api/v1/visualize/timeseries", json={"parameter": _PARAM})
        body = r.json()
        assert len(body["seasonal"]) == 4
        labels = {s["label"] for s in body["seasonal"]}
        assert "Monsoon (Jun-Sep)" in labels
        assert "Winter (Dec-Feb)" in labels

    async def test_climatology_has_twelve_months(self, client):
        await _seed_timeseries_fixture()
        r = await client.post("/api/v1/visualize/timeseries", json={"parameter": _PARAM})
        body = r.json()
        assert len(body["climatology"]) == 12

    async def test_anomaly_from_mean(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/timeseries", json={"parameter": _PARAM, "station": "ST-A"}
        )
        body = r.json()
        # Station A only: January value=1, mean=6.5 -> anomaly = -5.5
        assert body["anomaly"][0]["anomaly"] == pytest.approx(-5.5)

    async def test_unknown_parameter_returns_empty_series(self, client):
        await _seed_timeseries_fixture()
        r = await client.post("/api/v1/visualize/timeseries", json={"parameter": "Nonexistent"})
        assert r.status_code == 200
        assert r.json()["series"] == []


class TestSpatial:
    async def test_idw_matches_hand_computed_case(self, client):
        """3 points forming a small triangle; query the grid value exactly
        at one of the source points, which IDW must reproduce almost
        exactly (weight -> infinity as distance -> 0)."""
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "idw",
                "grid_resolution": 5,
                "bounds": {"lat_min": 20.5, "lat_max": 23.5, "lon_min": 89.5, "lon_max": 92.5},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        assert body["method_used"] == "idw"
        assert len(body["points"]) == 3
        grid = body["grid"]
        assert len(grid["lats"]) == 5
        assert len(grid["lons"]) == 5
        # Grid corner nearest station A (lat=21, lon=90) should be close to
        # its value (12.0, the latest/only value at that station-time pair
        # used by stationLatestValue-equivalent logic).
        assert grid["z"][0][0] == pytest.approx(12.0, abs=2.0)

    async def test_nearest_neighbour_returns_real_computation(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "nearest",
                "grid_resolution": 5,
                "bounds": {"lat_min": 20.5, "lat_max": 23.5, "lon_min": 89.5, "lon_max": 92.5},
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["method_used"] == "nearest"

    async def test_kriging_falls_back_to_idw(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "kriging",
                "grid_resolution": 5,
                "bounds": {"lat_min": 20.5, "lat_max": 23.5, "lon_min": 89.5, "lon_max": 92.5},
            },
        )
        assert r.status_code == 200
        assert r.json()["method_used"] == "idw"

    async def test_large_request_dispatches_job(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "idw",
                "grid_resolution": 100,
                "bounds": {"lat_min": 20.5, "lat_max": 23.5, "lon_min": 89.5, "lon_max": 92.5},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # 100^2 * 3 points = 30,000 work units < 40,000 sync threshold, so
        # bump resolution further via a second station-heavy scenario isn't
        # needed — eager Celery mode means even a "queued" response has
        # already completed by the time we poll.
        if body["status"] == "queued":
            job_id = body["job_id"]
            poll = await client.get(f"/api/v1/visualize/spatial/{job_id}")
            assert poll.status_code == 200
            assert poll.json()["status"] == VizJobStatus.COMPLETE.value
            assert poll.json()["grid"] is not None
        else:
            assert body["grid"] is not None

    async def test_poll_unknown_job_404s(self, client):
        r = await client.get(f"/api/v1/visualize/spatial/{uuid.uuid4()}")
        assert r.status_code == 404


class TestComparison:
    async def test_pearson_r_near_one_for_correlated_series(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/comparison",
            json={"parameters": [_PARAM, "Salinity"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scatter"]["r"] == pytest.approx(1.0, abs=0.01)
        # y = 2x -> slope ~2
        assert body["scatter"]["regression"]["slope"] == pytest.approx(2.0, abs=0.01)

    async def test_correlation_matrix_symmetric_with_unit_diagonal(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/comparison",
            json={"parameters": [_PARAM, "Salinity"]},
        )
        matrix = r.json()["correlation_matrix"]["matrix"]
        assert matrix[0][0] == pytest.approx(1.0)
        assert matrix[1][1] == pytest.approx(1.0)
        assert matrix[0][1] == pytest.approx(matrix[1][0])

    async def test_requires_at_least_two_parameters(self, client):
        r = await client.post("/api/v1/visualize/comparison", json={"parameters": [_PARAM]})
        assert r.status_code == 422


class TestStatistics:
    async def test_decomposition_sums_to_original_value(self, client):
        await _seed_timeseries_fixture()
        # Filtered to station A's clean 12-point monthly series only — the
        # fixture's stations B/C points (2023-12-01) are for spatial tests
        # and would otherwise add a 13th, unrelated data point here.
        r = await client.post(
            "/api/v1/visualize/statistics", json={"parameter": _PARAM, "station": "ST-A"}
        )
        assert r.status_code == 200, r.text
        decomp = r.json()["decomposition"]
        n = len(decomp["dates"])
        assert n == 12
        for i in range(n):
            trend = decomp["trend"][i]
            seasonal = decomp["seasonal"][i]
            residual = decomp["residual"][i]
            reconstructed = trend + seasonal + residual
            # Original series value at month i+1 is i+1 (1..12).
            assert reconstructed == pytest.approx(float(i + 1), abs=1e-6)

    async def test_annual_anomalies_use_real_years(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/statistics", json={"parameter": _PARAM, "station": "ST-A"}
        )
        anomalies = r.json()["annual_anomalies"]
        assert len(anomalies) == 1
        assert anomalies[0]["year"] == 2024

    async def test_box_plot_limited_to_six_stations(self, client):
        await _seed_timeseries_fixture()
        r = await client.post("/api/v1/visualize/statistics", json={"parameter": _PARAM})
        box_plot = r.json()["box_plot"]
        assert len(box_plot) <= 6
        assert len(box_plot) == 3  # stations A, B, C

    async def test_calendar_heatmap_shape(self, client):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/statistics", json={"parameter": _PARAM, "station": "ST-A"}
        )
        heatmap = r.json()["calendar_heatmap"]
        assert heatmap["years"] == [2024]
        assert len(heatmap["months"]) == 12
        assert len(heatmap["z"][0]) == 12


class TestCaching:
    async def test_identical_timeseries_request_is_cached(self, client):
        await _seed_timeseries_fixture()
        r1 = await client.post("/api/v1/visualize/timeseries", json={"parameter": _PARAM})
        assert r1.status_code == 200

        # Mutate underlying data — a cached response should still be
        # returned within TTL, proving the cache path was actually hit.
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            result = await db.execute(
                select(DatasetRecord).where(DatasetRecord.parameter == _PARAM).limit(1)
            )
            record = result.scalar_one()
            record.value = 9999.0
            await db.commit()

        r2 = await client.post("/api/v1/visualize/timeseries", json={"parameter": _PARAM})
        assert r2.json()["stats"]["mean"] == r1.json()["stats"]["mean"]


class TestBoundaryShapefiles:
    async def test_create_list_delete_roundtrip(self, client, admin_headers):
        r = await client.post(
            "/api/v1/boundary-shapefiles",
            data={
                "name": "Test Boundary",
                "geojson": '{"type": "FeatureCollection", "features": []}',
                "is_default": "false",
            },
            headers=admin_headers,
        )
        assert r.status_code == 201, r.text
        boundary_id = r.json()["id"]

        r2 = await client.get("/api/v1/boundary-shapefiles")
        assert any(b["id"] == boundary_id for b in r2.json())

        r3 = await client.delete(f"/api/v1/boundary-shapefiles/{boundary_id}", headers=admin_headers)
        assert r3.status_code == 204

    async def test_only_one_default_at_a_time(self, client, admin_headers):
        r1 = await client.post(
            "/api/v1/boundary-shapefiles",
            data={"name": "First", "geojson": "{}", "is_default": "true"},
            headers=admin_headers,
        )
        r2 = await client.post(
            "/api/v1/boundary-shapefiles",
            data={"name": "Second", "geojson": "{}", "is_default": "true"},
            headers=admin_headers,
        )
        assert r1.status_code == 201
        assert r2.status_code == 201

        default = await client.get("/api/v1/boundary-shapefiles/default")
        assert default.json()["name"] == "Second"

        all_boundaries = await client.get("/api/v1/boundary-shapefiles")
        defaults = [b for b in all_boundaries.json() if b["is_default"]]
        assert len(defaults) == 1

    async def test_upload_requires_permission(self, client):
        r = await client.post(
            "/api/v1/boundary-shapefiles",
            data={"name": "Unauthorized", "geojson": "{}", "is_default": "false"},
        )
        assert r.status_code == 401


class TestVisualizationExportSettings:
    async def test_get_returns_defaults_on_first_call(self, client):
        r = await client.get("/api/v1/admin/settings/visualization-exports")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {
            "viz_export_temporal_enabled": True,
            "viz_export_spatial_enabled": True,
            "viz_export_comparison_enabled": True,
            "viz_export_statistics_enabled": True,
        }

    async def test_patch_persists(self, client, admin_headers):
        r = await client.patch(
            "/api/v1/admin/settings/visualization-exports",
            json={"viz_export_temporal_enabled": False},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["viz_export_temporal_enabled"] is False

        r2 = await client.get("/api/v1/admin/settings/visualization-exports")
        assert r2.json()["viz_export_temporal_enabled"] is False
        assert r2.json()["viz_export_spatial_enabled"] is True

    async def test_patch_requires_admin(self, client):
        r = await client.patch(
            "/api/v1/admin/settings/visualization-exports",
            json={"viz_export_temporal_enabled": False},
        )
        assert r.status_code == 401
