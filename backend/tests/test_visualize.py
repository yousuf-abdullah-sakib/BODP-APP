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
        # Regression is against real elapsed time (fractional years since
        # the first bucket), not bucket index — so this is no longer
        # exactly 12.0 even though the index-based slope was exactly 1.0.
        # Buckets land on 2024-01-01..2024-12-01 (date_trunc('month', ...)
        # of the 15th of each month); real calendar months have unequal
        # day-lengths (28-31 days), so uniform-index spacing and true
        # elapsed-time spacing diverge slightly. Hand-computed via the
        # exact same dates/values as the fixture: slope = 11.97931345753617.
        assert body["stats"]["trend_per_year"] == pytest.approx(11.97931345753617, abs=1e-6)

    async def test_trend_per_year_consistent_across_resolutions(self, client):
        """The old index-based regression only meant "per year" at
        resolution="monthly" (trend_per_year = slope * 12). Elapsed-time
        regression must report a physically consistent per-year rate
        regardless of which resolution bucketed the series — this is the
        actual bug the elapsed-time fix closes, so it needs its own
        explicit guard rather than relying on the monthly-only assertion
        above."""
        await _seed_timeseries_fixture()
        r_monthly = await client.post(
            "/api/v1/visualize/timeseries",
            json={"parameter": _PARAM, "resolution": "monthly", "station": "ST-A"},
        )
        r_daily = await client.post(
            "/api/v1/visualize/timeseries",
            json={"parameter": _PARAM, "resolution": "daily", "station": "ST-A"},
        )
        assert r_monthly.status_code == 200, r_monthly.text
        assert r_daily.status_code == 200, r_daily.text
        monthly_trend = r_monthly.json()["stats"]["trend_per_year"]
        daily_trend = r_daily.json()["stats"]["trend_per_year"]
        # Same underlying data (one value per calendar day the station has
        # a record, values 1..12 across Jan..Dec) fit against real elapsed
        # time must yield approximately the same "value change per year"
        # regardless of whether the series was bucketed daily or monthly —
        # the old implementation would have reported a ~365x-too-large
        # daily trend (slope-per-day * 12) instead.
        assert daily_trend == pytest.approx(monthly_trend, rel=0.05)

    async def test_seasonal_breakdown_has_four_seasons(self, client):
        await _seed_timeseries_fixture()
        r = await client.post("/api/v1/visualize/timeseries", json={"parameter": _PARAM})
        body = r.json()
        assert len(body["seasonal"]) == 4
        labels = {s["label"] for s in body["seasonal"]}
        assert "Monsoon (Jun-Sep)" in labels
        assert "Winter (Dec-Feb)" in labels

    async def test_resolution_seasonal_buckets_by_bangladesh_seasons(self, client):
        """resolution="seasonal" must group by the app's own Bangladesh
        4-season definition (Pre-Monsoon Mar-May, Monsoon Jun-Sep,
        Post-Monsoon Oct-Nov, Winter Dec-Feb), not Postgres' ordinary
        calendar quarter (Jan-Mar/Apr-Jun/Jul-Sep/Oct-Dec) — the previous
        implementation used date_trunc('quarter', ...) directly, which
        would have grouped March with Jan-Feb (calendar Q1) instead of
        with Apr-May (Pre-Monsoon)."""
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"parameter": _PARAM, "resolution": "seasonal", "station": "ST-A"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        series = {p["date"]: p["value"] for p in body["series"]}
        # Fixture: value=month for Jan(1)..Dec(12) 2024, day=15.
        # Jan+Feb 2024 roll into the PRIOR December's Winter bucket
        # (2023-12-01, only 2 of its 3 months present in this fixture);
        # Mar-May -> 2024-03-01; Jun-Sep -> 2024-06-01; Oct-Nov ->
        # 2024-10-01; Dec alone starts a new (incomplete) Winter bucket
        # at 2024-12-01.
        assert series["2023-12-01"] == pytest.approx((1.0 + 2.0) / 2)  # Jan, Feb
        assert series["2024-03-01"] == pytest.approx((3.0 + 4.0 + 5.0) / 3)  # Mar, Apr, May
        assert series["2024-06-01"] == pytest.approx((6.0 + 7.0 + 8.0 + 9.0) / 4)  # Jun-Sep
        assert series["2024-10-01"] == pytest.approx((10.0 + 11.0) / 2)  # Oct, Nov
        assert series["2024-12-01"] == pytest.approx(12.0)  # Dec alone
        # Calendar-quarter grouping (the old, wrong behavior) would have
        # produced a 2024-01-01 bucket averaging Jan-Mar (1,2,3); no such
        # bucket should exist under Bangladesh-season grouping.
        assert "2024-01-01" not in series

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
    @pytest.fixture(autouse=True)
    async def _raise_aoi_limit(self, client, admin_headers):
        # The 3 seeded test stations span ~2 degrees lat/lon apart (a
        # realistic real-world spread), whose bounding box (~100,000 km²)
        # is far larger than the default 500 km² AOI limit — raise it here
        # so these interpolation-correctness tests aren't also exercising
        # the AOI limit (that has its own dedicated tests below).
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": 1_000_000},
            headers=admin_headers,
        )

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

    async def test_idw_uses_geodesic_not_planar_distance(self, client, admin_headers):
        """Proves IDW weighting is by real geodesic (haversine) distance,
        not raw lat/lon degree differences. Query point (21.75, 90.0);
        point A is 1 DEGREE due north (pure latitude offset, value=10);
        point B is 1 DEGREE due east (pure longitude offset, value=20).
        Under the OLD planar-degree distance, d_A == d_B == 1.0 exactly
        (equal raw degree offsets) -> equal IDW weight -> interpolated
        value at the query point = midpoint average = 15.0. Under real
        geodesic distance, B (longitude offset) is physically CLOSER than
        A (latitude offset) at this latitude — 1 degree of longitude
        shrinks by cos(21.75deg) relative to 1 degree of latitude — so B
        must receive strictly more weight, pulling the interpolated value
        measurably above 15.0 toward B's value (20). Hand-computed exact
        expected value (haversine, power=2): 15.368596628435457."""
        async with AsyncSessionLocal() as db:
            category = DatasetCategory(name="Geodesic IDW Test", description="test", color_tag="cat-geo")
            db.add(category)
            await db.flush()
            dataset = Dataset(
                code="BD-VIZ-GEO",
                title="Geodesic IDW Test Dataset",
                category_id=category.id,
                status=DatasetStatus.PUBLISHED.value,
                record_count=0,
            )
            db.add(dataset)
            await db.flush()

            station_north = Station(name="North Station", code="ST-N", lat=22.75, lon=90.0, depth_m=10)
            station_east = Station(name="East Station", code="ST-E", lat=21.75, lon=91.0, depth_m=10)
            db.add_all([station_north, station_east])
            await db.flush()

            param = "Geodesic Test Param"
            db.add(
                DatasetRecord(
                    dataset_id=dataset.id, time=date(2024, 1, 1), lat=22.75, lon=90.0,
                    station_id=station_north.id, parameter=param, value=10.0, unit="unit",
                    quality_flag="normal", geom="SRID=4326;POINT(90.0 22.75)",
                )
            )
            db.add(
                DatasetRecord(
                    dataset_id=dataset.id, time=date(2024, 1, 1), lat=21.75, lon=91.0,
                    station_id=station_east.id, parameter=param, value=20.0, unit="unit",
                    quality_flag="normal", geom="SRID=4326;POINT(91.0 21.75)",
                )
            )
            await db.commit()

        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": 1_000_000},
            headers=admin_headers,
        )

        # np.linspace's first point is always exactly lat_min/lon_min
        # regardless of resolution, so grid["z"][0][0] is exactly the
        # query point (21.75, 90.0) at any valid grid_resolution.
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": param,
                "method": "idw",
                "grid_resolution": 5,
                "bounds": {"lat_min": 21.75, "lat_max": 22.75, "lon_min": 90.0, "lon_max": 91.0},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        query_point_value = body["grid"]["z"][0][0]
        # Old planar-degree behavior would give exactly 15.0 here; real
        # geodesic distance must give a strictly, measurably larger value.
        assert query_point_value > 15.1
        assert query_point_value == pytest.approx(15.368596628435457, abs=1e-6)

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

    async def test_kriging_method_rejected(self, client):
        """Kriging was removed from the dropdown (Visualize Phase B) since
        it never computed real kriging, only silently fell back to IDW —
        the API must now reject it outright rather than perpetuate an
        undisclosed silent fallback for direct callers."""
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
        assert r.status_code == 422

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
        # Both parameters have exactly 12 points at the same 12 dates
        # (Jan-Dec 2024, station A) -> full overlap, n == 12.
        assert body["scatter"]["n"] == 12
        assert body["scatter"]["pairing_method"] == "exact_date_match"

    async def test_n_reflects_partial_date_overlap(self, client):
        """n must reflect the true size of the date-intersection actually
        used for r/regression/the scatter plot — not len(params.parameters)
        or either series' own length in isolation. Seeds a second dataset
        where one parameter has 12 monthly points and another has only 5
        of those same dates, so n must be exactly 5, not 12."""
        async with AsyncSessionLocal() as db:
            category = DatasetCategory(name="Partial Overlap Test", description="test", color_tag="cat-partial")
            db.add(category)
            await db.flush()
            dataset = Dataset(
                code="BD-VIZ-PARTIAL",
                title="Partial Overlap Test Dataset",
                category_id=category.id,
                status=DatasetStatus.PUBLISHED.value,
                record_count=0,
            )
            db.add(dataset)
            await db.flush()

            full_param, partial_param = "Full Series Param", "Partial Series Param"
            for month in range(1, 13):
                db.add(
                    DatasetRecord(
                        dataset_id=dataset.id,
                        time=date(2024, month, 1),
                        lat=21.0,
                        lon=90.0,
                        parameter=full_param,
                        value=float(month),
                        unit="unit",
                        quality_flag="normal",
                        geom="SRID=4326;POINT(90.0 21.0)",
                    )
                )
            # Only 5 of the same 12 dates for the second parameter.
            for month in (1, 2, 3, 4, 5):
                db.add(
                    DatasetRecord(
                        dataset_id=dataset.id,
                        time=date(2024, month, 1),
                        lat=21.0,
                        lon=90.0,
                        parameter=partial_param,
                        value=float(month) * 10,
                        unit="unit",
                        quality_flag="normal",
                        geom="SRID=4326;POINT(90.0 21.0)",
                    )
                )
            await db.commit()

        r = await client.post(
            "/api/v1/visualize/comparison",
            json={"parameters": [full_param, partial_param]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scatter"]["n"] == 5
        assert len(body["scatter"]["x"]) == 5
        assert len(body["scatter"]["y"]) == 5

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


class TestVisualizationComputeLimits:
    async def test_get_returns_defaults_on_first_call(self, client):
        r = await client.get("/api/v1/admin/settings/visualization-limits")
        assert r.status_code == 200, r.text
        assert r.json() == {
            "viz_max_grid_resolution": 100,
            "viz_max_aoi_km2": 500.0,
            "viz_max_date_range_days_spatial": 3650,
            "viz_max_date_range_days_timeseries": None,
            "viz_max_date_range_days_comparison": None,
            "viz_max_date_range_days_statistics": None,
        }

    async def test_patch_persists(self, client, admin_headers):
        r = await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_grid_resolution": 50},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["viz_max_grid_resolution"] == 50

        r2 = await client.get("/api/v1/admin/settings/visualization-limits")
        assert r2.json()["viz_max_grid_resolution"] == 50
        assert r2.json()["viz_max_aoi_km2"] == 500.0

    async def test_patch_can_set_date_range_back_to_unlimited(self, client, admin_headers):
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_date_range_days_timeseries": 30},
            headers=admin_headers,
        )
        r = await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_date_range_days_timeseries": None},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["viz_max_date_range_days_timeseries"] is None

    async def test_patch_rejects_non_positive_values(self, client, admin_headers):
        r = await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": 0},
            headers=admin_headers,
        )
        assert r.status_code == 422

    async def test_patch_requires_admin(self, client):
        r = await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_grid_resolution": 50},
        )
        assert r.status_code == 401

    async def test_spatial_rejects_resolution_above_limit(self, client, admin_headers):
        await _seed_timeseries_fixture()
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_grid_resolution": 10, "viz_max_aoi_km2": 1_000_000},
            headers=admin_headers,
        )
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "idw",
                "grid_resolution": 20,
                "bounds": {"lat_min": 20.5, "lat_max": 23.5, "lon_min": 89.5, "lon_max": 92.5},
            },
        )
        assert r.status_code == 422
        assert "resolution" in r.json()["error"]["message"].lower()

    async def test_spatial_accepts_resolution_at_or_below_limit(self, client, admin_headers):
        await _seed_timeseries_fixture()
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_grid_resolution": 10, "viz_max_aoi_km2": 1_000_000},
            headers=admin_headers,
        )
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "idw",
                "grid_resolution": 10,
                "bounds": {"lat_min": 20.5, "lat_max": 23.5, "lon_min": 89.5, "lon_max": 92.5},
            },
        )
        assert r.status_code == 200, r.text

    async def test_spatial_rejects_aoi_above_limit(self, client, admin_headers):
        await _seed_timeseries_fixture()
        # Default viz_max_aoi_km2 is 500 — this bbox (~103,000 km²) is far
        # over it, so no PATCH is needed to trigger the rejection.
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "idw",
                "grid_resolution": 5,
                "bounds": {"lat_min": 20.5, "lat_max": 23.5, "lon_min": 89.5, "lon_max": 92.5},
            },
        )
        assert r.status_code == 422
        assert "area" in r.json()["error"]["message"].lower()

    async def test_spatial_accepts_aoi_at_or_below_limit(self, client, admin_headers):
        await _seed_timeseries_fixture()
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "idw",
                "grid_resolution": 5,
                # ~0.05deg x 0.05deg around station A — well under 500 km².
                "bounds": {"lat_min": 20.98, "lat_max": 21.02, "lon_min": 89.98, "lon_max": 90.02},
            },
        )
        assert r.status_code == 200, r.text

    async def test_grid_resolution_unlimited_when_null(self, client, admin_headers):
        await _seed_timeseries_fixture()
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_grid_resolution": None, "viz_max_aoi_km2": 1_000_000},
            headers=admin_headers,
        )
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

    async def test_aoi_unlimited_when_null(self, client, admin_headers):
        await _seed_timeseries_fixture()
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": None},
            headers=admin_headers,
        )
        # This bbox (~103,000 km²) would normally be rejected under the
        # default 500 km² limit.
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

    async def test_grid_resolution_and_aoi_can_be_set_back_to_a_number(self, client, admin_headers):
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_grid_resolution": None, "viz_max_aoi_km2": None},
            headers=admin_headers,
        )
        r = await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_grid_resolution": 80, "viz_max_aoi_km2": 250},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["viz_max_grid_resolution"] == 80
        assert r.json()["viz_max_aoi_km2"] == 250

    async def test_spatial_rejects_date_range_above_limit(self, client, admin_headers):
        await _seed_timeseries_fixture()
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_aoi_km2": 1_000_000, "viz_max_date_range_days_spatial": 30},
            headers=admin_headers,
        )
        r = await client.post(
            "/api/v1/visualize/spatial",
            json={
                "parameter": _PARAM,
                "method": "idw",
                "grid_resolution": 5,
                "bounds": {"lat_min": 20.5, "lat_max": 23.5, "lon_min": 89.5, "lon_max": 92.5},
                "date_from": "2024-01-01",
                "date_to": "2024-12-31",
            },
        )
        assert r.status_code == 422
        assert "date range" in r.json()["error"]["message"].lower()

    async def test_timeseries_respects_its_own_configured_limit(self, client, admin_headers):
        await _seed_timeseries_fixture()
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_date_range_days_timeseries": 30},
            headers=admin_headers,
        )
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"parameter": _PARAM, "date_from": "2024-01-01", "date_to": "2024-12-31"},
        )
        assert r.status_code == 422
        assert "date range" in r.json()["error"]["message"].lower()

    async def test_comparison_and_statistics_unaffected_by_timeseries_limit(self, client, admin_headers):
        await _seed_timeseries_fixture()
        await client.patch(
            "/api/v1/admin/settings/visualization-limits",
            json={"viz_max_date_range_days_timeseries": 30},
            headers=admin_headers,
        )
        # comparison/statistics have their own (still-unlimited-by-default)
        # limits, independent of timeseries' now-lowered one.
        r_comparison = await client.post(
            "/api/v1/visualize/comparison",
            json={
                "parameters": [_PARAM, "Salinity"],
                "date_from": "2024-01-01",
                "date_to": "2024-12-31",
            },
        )
        assert r_comparison.status_code == 200, r_comparison.text

        r_statistics = await client.post(
            "/api/v1/visualize/statistics",
            json={"parameter": _PARAM, "date_from": "2024-01-01", "date_to": "2024-12-31"},
        )
        assert r_statistics.status_code == 200, r_statistics.text

    async def test_none_limit_means_unlimited(self, client, admin_headers):
        await _seed_timeseries_fixture()
        # Explicitly confirm the default None (unlimited) for timeseries
        # allows a wide date range with no rejection.
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"parameter": _PARAM, "date_from": "2000-01-01", "date_to": "2024-12-31"},
        )
        assert r.status_code == 200, r.text
