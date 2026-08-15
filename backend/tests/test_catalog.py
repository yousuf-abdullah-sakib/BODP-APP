import uuid
from datetime import date

import pytest

from app.core.database import AsyncSessionLocal
from app.core.limiter import limiter
from app.models.catalog import (
    Dataset,
    DatasetCategory,
    DatasetRecord,
    DatasetStatus,
    QualityFlag,
    Station,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    limiter.enabled = False


async def _seed_minimal_catalog():
    """Seeds a small, fully-controlled dataset set directly via the ORM —
    deliberately independent of app/scripts/seed_catalog.py (that script is
    for local dev exploration; tests need exact, known values to assert
    against)."""
    async with AsyncSessionLocal() as db:
        env_category = DatasetCategory(
            name="Environmental", description="test", color_tag="cat-Environmental"
        )
        pollution_category = DatasetCategory(
            name="Pollution", description="test", color_tag="cat-Pollution"
        )
        db.add_all([env_category, pollution_category])
        await db.flush()

        station_a = Station(name="Cox's Bazar", code="ST-COXB", lat=21.4272, lon=92.0058, depth_m=12)
        station_b = Station(name="Chittagong", code="ST-CTG", lat=22.3569, lon=91.7832, depth_m=18)
        db.add_all([station_a, station_b])
        await db.flush()

        published = Dataset(
            code="BD-TEST-001",
            title="Bay of Bengal SST Test Dataset",
            description="Sea surface temperature and salinity test dataset.",
            category_id=env_category.id,
            location="Bay of Bengal",
            source="BORI Station",
            platforms=["Fixed Buoy", "Satellite"],
            parameters=["Sea Surface Temp", "Salinity"],
            resolution="Daily",
            license="CC BY 4.0",
            processing_levels=["L1 — Calibrated"],
            formats=["CSV", "NetCDF"],
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        draft_dataset = Dataset(
            code="BD-TEST-002",
            title="Unpublished Draft Dataset",
            description="Should never appear in public results.",
            category_id=env_category.id,
            location="Bay of Bengal",
            source="BORI Station",
            platforms=["Fixed Buoy"],
            parameters=["Sea Surface Temp"],
            resolution="Daily",
            license="CC BY 4.0",
            processing_levels=["L1 — Calibrated"],
            formats=["CSV"],
            status=DatasetStatus.DRAFT.value,
            record_count=0,
        )
        archived_dataset = Dataset(
            code="BD-TEST-003",
            title="Archived Dataset",
            description="Should never appear in public results.",
            category_id=pollution_category.id,
            location="Chittagong",
            source="Coastal Survey",
            platforms=["Research Vessel"],
            parameters=["Heavy Metals (Pb)"],
            resolution="Monthly",
            license="CC BY 4.0",
            processing_levels=["L0 — Raw"],
            formats=["CSV"],
            status=DatasetStatus.ARCHIVED.value,
            record_count=0,
        )
        pollution_dataset = Dataset(
            code="BD-TEST-004",
            title="Chittagong Microplastics Survey",
            description="Coastal microplastics baseline near Chittagong.",
            category_id=pollution_category.id,
            location="Chittagong",
            source="Coastal Survey",
            platforms=["Research Vessel"],
            parameters=["Microplastics"],
            resolution="Weekly",
            license="CC BY 4.0",
            processing_levels=["L1 — Calibrated"],
            formats=["Excel"],
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add_all([published, draft_dataset, archived_dataset, pollution_dataset])
        await db.flush()

        # 10 records for the published SST dataset: 2 stations, split across
        # a known lat range and a known quality-flag distribution so spatial
        # filtering and the quality breakdown can be asserted exactly.
        lat_min = lat_max = lon_min = lon_max = None
        temporal_start = temporal_end = None
        for i in range(10):
            station = station_a if i < 6 else station_b
            record_date = date(2024, 1, 1 + i)
            lat = float(station.lat) + (i * 0.001)
            lon = float(station.lon) + (i * 0.001)
            quality = (
                QualityFlag.ALERT.value
                if i == 0
                else QualityFlag.CAUTION.value
                if i in (1, 2)
                else QualityFlag.NORMAL.value
            )
            db.add(
                DatasetRecord(
                    dataset_id=published.id,
                    time=record_date,
                    lat=lat,
                    lon=lon,
                    depth_m=10.0 + i,
                    location=station.name,
                    station_id=station.id,
                    parameter="Sea Surface Temp" if i % 2 == 0 else "Salinity",
                    value=25.0 + i,
                    unit="°C",
                    quality_flag=quality,
                    processing_level="L1 — Calibrated",
                    format="CSV",
                    source="BORI Station",
                    platform="Fixed Buoy",
                    geom=f"SRID=4326;POINT({lon} {lat})",
                )
            )
            lat_min = lat if lat_min is None else min(lat_min, lat)
            lat_max = lat if lat_max is None else max(lat_max, lat)
            lon_min = lon if lon_min is None else min(lon_min, lon)
            lon_max = lon if lon_max is None else max(lon_max, lon)
            temporal_start = record_date if temporal_start is None else min(temporal_start, record_date)
            temporal_end = record_date if temporal_end is None else max(temporal_end, record_date)

        published.record_count = 10
        published.temporal_start = temporal_start
        published.temporal_end = temporal_end
        published.spatial_extent = (
            f"SRID=4326;POLYGON(({lon_min} {lat_min}, {lon_max} {lat_min}, "
            f"{lon_max} {lat_max}, {lon_min} {lat_max}, {lon_min} {lat_min}))"
        )

        await db.commit()

        return {
            "published_id": published.id,
            "draft_id": draft_dataset.id,
            "archived_id": archived_dataset.id,
            "pollution_id": pollution_dataset.id,
            "station_a_code": station_a.code,
            "station_b_code": station_b.code,
            "bbox": {
                "lat_min": lat_min,
                "lat_max": lat_max,
                "lon_min": lon_min,
                "lon_max": lon_max,
            },
        }


class TestCatalogSearch:
    async def test_search_returns_only_published_datasets(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        titles = {d["title"] for d in body["results"]}
        assert titles == {"Bay of Bengal SST Test Dataset", "Chittagong Microplastics Survey"}

    async def test_draft_dataset_never_visible(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search")
        ids = {d["id"] for d in r.json()["results"]}
        assert str(seed["draft_id"]) not in ids

    async def test_archived_dataset_never_visible(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search")
        ids = {d["id"] for d in r.json()["results"]}
        assert str(seed["archived_id"]) not in ids

    async def test_draft_dataset_detail_404s_even_with_direct_id(self, client):
        """A draft dataset must be unreachable via /catalog/{id} too, not
        just hidden from search — otherwise its ID leaking anywhere (logs,
        browser history) would expose unpublished data."""
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['draft_id']}")
        assert r.status_code == 404

    async def test_search_by_title_exact_match_ranks_first(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"search": "chittagong microplastics survey"})
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Chittagong Microplastics Survey"

    async def test_search_by_parameter_substring(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"search": "microplastics"})
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Chittagong Microplastics Survey"

    async def test_search_by_platform_substring(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"search": "satellite"})
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Bay of Bengal SST Test Dataset"

    async def test_search_no_match_returns_empty(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"search": "nonexistent xyz query"})
        assert r.json()["total"] == 0

    async def test_filter_by_category(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"category": "Pollution"})
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Chittagong Microplastics Survey"

    async def test_filter_by_parameter(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"parameter": "Salinity"})
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Bay of Bengal SST Test Dataset"

    async def test_filter_by_source(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"source": "Coastal Survey"})
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Chittagong Microplastics Survey"

    async def test_filter_by_platform(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"platform": "Research Vessel"})
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Chittagong Microplastics Survey"

    async def test_filter_by_format(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"format": "NetCDF"})
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Bay of Bengal SST Test Dataset"

    async def test_combined_filters_use_and_logic(self, client):
        await _seed_minimal_catalog()
        r = await client.get(
            "/api/v1/catalog/search",
            params={"category": "Environmental", "format": "Excel"},
        )
        # No dataset is both Environmental AND has an Excel format.
        assert r.json()["total"] == 0

    async def test_sort_by_title(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"sort": "title"})
        titles = [d["title"] for d in r.json()["results"]]
        assert titles == sorted(titles)

    async def test_sort_by_records(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/search", params={"sort": "records"})
        results = r.json()["results"]
        # The SST dataset has 10 records seeded, the pollution one has 0.
        assert results[0]["title"] == "Bay of Bengal SST Test Dataset"


class TestCatalogDetail:
    async def test_detail_returns_expected_fields(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['published_id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "Bay of Bengal SST Test Dataset"
        assert body["category"] == "Environmental"
        assert set(body["parameters"]) == {"Sea Surface Temp", "Salinity"}
        assert body["record_count"] == 10
        assert body["temporal_start"] == "2024-01-01"
        assert body["temporal_end"] == "2024-01-10"

    async def test_detail_spatial_bbox_matches_seeded_records(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['published_id']}")
        bbox = r.json()["spatial_bbox"]
        expected = seed["bbox"]
        assert bbox["lat_min"] == pytest.approx(expected["lat_min"])
        assert bbox["lat_max"] == pytest.approx(expected["lat_max"])
        assert bbox["lon_min"] == pytest.approx(expected["lon_min"])
        assert bbox["lon_max"] == pytest.approx(expected["lon_max"])

    async def test_detail_unknown_id_404s(self, client):
        r = await client.get(f"/api/v1/catalog/{uuid.uuid4()}")
        assert r.status_code == 404

    async def test_stations_only_returns_stations_present_in_dataset(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['published_id']}/stations")
        assert r.status_code == 200
        codes = {s["code"] for s in r.json()}
        assert codes == {seed["station_a_code"], seed["station_b_code"]}

    async def test_stations_for_dataset_with_no_records_is_empty(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['pollution_id']}/stations")
        assert r.status_code == 200
        assert r.json() == []


class TestCatalogRecords:
    async def test_records_no_filter_returns_all_matching(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['published_id']}/records")
        assert r.status_code == 200
        body = r.json()
        assert body["matching_count"] == 10
        assert body["dataset_total_count"] == 10
        assert len(body["preview"]) == 6  # capped preview

    async def test_records_quality_breakdown_is_exact(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['published_id']}/records")
        breakdown = r.json()["quality_breakdown"]
        assert breakdown == {"normal": 7, "caution": 2, "alert": 1}

    async def test_records_filter_by_single_parameter(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(
            f"/api/v1/catalog/{seed['published_id']}/records", params={"parameters": ["Salinity"]}
        )
        body = r.json()
        # 5 of the 10 seeded records alternate to Salinity (odd indices).
        assert body["matching_count"] == 5
        assert all(row["parameter"] == "Salinity" for row in body["preview"])

    async def test_records_filter_by_multiple_parameters_returns_union(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(
            f"/api/v1/catalog/{seed['published_id']}/records",
            params={"parameters": ["Salinity", "Sea Surface Temp"]},
        )
        body = r.json()
        # Both seeded parameter values selected — union covers all 10 rows,
        # same as no parameter filter at all (checkbox multi-select: zero
        # selected == all included, and selecting every option explicitly
        # must behave identically).
        assert body["matching_count"] == 10

    async def test_records_no_parameter_selected_returns_all(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['published_id']}/records")
        body = r.json()
        assert body["matching_count"] == 10

    async def test_records_filter_by_quality(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(
            f"/api/v1/catalog/{seed['published_id']}/records", params={"quality": "alert"}
        )
        body = r.json()
        assert body["matching_count"] == 1

    async def test_records_filter_by_date_range(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(
            f"/api/v1/catalog/{seed['published_id']}/records",
            params={"date_from": "2024-01-05", "date_to": "2024-01-07"},
        )
        body = r.json()
        assert body["matching_count"] == 3

    async def test_records_filter_by_station(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(
            f"/api/v1/catalog/{seed['published_id']}/records",
            params={"station": seed["station_a_code"]},
        )
        body = r.json()
        assert body["matching_count"] == 6

    async def test_records_spatial_filter_matches_manual_bbox_subset(self, client):
        """Quality check from Master Plan §3 Phase 3: 'spatial filter (drawn
        rectangle) returns the same records a manual PostGIS query would.'
        Station A (Cox's Bazar) covers the first 6 records; restricting the
        bbox to just around station A's coordinates should return exactly
        those 6, verified independently via a direct SQL count."""
        seed = await _seed_minimal_catalog()
        bbox = seed["bbox"]
        # Station A's records occupy roughly the lower lat/lon sub-range
        # (station A lat ~21.42, station B lat ~22.36) — restrict to a bbox
        # that only covers station A's cluster.
        r = await client.get(
            f"/api/v1/catalog/{seed['published_id']}/records",
            params={
                "lat_min": bbox["lat_min"],
                "lat_max": 22.0,
                "lon_min": bbox["lon_min"],
                "lon_max": bbox["lon_max"],
            },
        )
        body = r.json()

        from sqlalchemy import func, select

        async with AsyncSessionLocal() as db:
            envelope = func.ST_MakeEnvelope(
                bbox["lon_min"], bbox["lat_min"], bbox["lon_max"], 22.0, 4326
            )
            manual_count = (
                await db.execute(
                    select(func.count())
                    .select_from(DatasetRecord)
                    .where(
                        DatasetRecord.dataset_id == seed["published_id"],
                        func.ST_Intersects(DatasetRecord.geom, envelope),
                    )
                )
            ).scalar_one()

        assert body["matching_count"] == manual_count
        assert body["matching_count"] == 6  # station A's 6 records

    async def test_records_unknown_dataset_404s(self, client):
        r = await client.get(f"/api/v1/catalog/{uuid.uuid4()}/records")
        assert r.status_code == 404

    async def test_records_draft_dataset_404s(self, client):
        seed = await _seed_minimal_catalog()
        r = await client.get(f"/api/v1/catalog/{seed['draft_id']}/records")
        assert r.status_code == 404


class TestCatalogTaxonomy:
    async def test_taxonomy_only_reflects_published_datasets(self, client):
        await _seed_minimal_catalog()
        r = await client.get("/api/v1/catalog/taxonomy")
        assert r.status_code == 200
        body = r.json()
        assert set(body["categories"]) == {"Environmental", "Pollution"}
        assert "Heavy Metals (Pb)" not in body["parameters"]  # only on the archived dataset
        assert set(body["parameters"]) == {"Sea Surface Temp", "Salinity", "Microplastics"}
        assert set(body["sources"]) == {"BORI Station", "Coastal Survey"}
        assert set(body["formats"]) == {"CSV", "NetCDF", "Excel"}

    async def test_taxonomy_is_cached(self, client):
        """Second call within the TTL should be served from cache — verified
        indirectly by confirming a DB-level change doesn't appear until the
        cache is invalidated, proving the cache path is actually hit rather
        than always recomputing (Master Plan §3 Phase 3 task 6)."""
        await _seed_minimal_catalog()
        r1 = await client.get("/api/v1/catalog/taxonomy")
        assert set(r1.json()["categories"]) == {"Environmental", "Pollution"}

        # Add a new published dataset in a brand-new category directly via DB.
        async with AsyncSessionLocal() as db:
            new_category = DatasetCategory(name="Atmospheric", description="", color_tag="cat-Atmospheric")
            db.add(new_category)
            await db.flush()
            db.add(
                Dataset(
                    code="BD-TEST-999",
                    title="New Atmospheric Dataset",
                    category_id=new_category.id,
                    status=DatasetStatus.PUBLISHED.value,
                    parameters=[],
                    platforms=[],
                    formats=[],
                    processing_levels=[],
                    record_count=0,
                )
            )
            await db.commit()

        r2 = await client.get("/api/v1/catalog/taxonomy")
        # Still cached — the new category should NOT appear yet.
        assert "Atmospheric" not in r2.json()["categories"]
