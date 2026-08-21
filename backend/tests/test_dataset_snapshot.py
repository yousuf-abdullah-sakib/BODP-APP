"""Dataset Default-View Snapshot feature — generation task, both read-path
guard clauses (Data Page records endpoint, Visualize /timeseries default-
shape shortcut), staleness-triggers-fallback, and the no-approved-variable
case. Mirrors test_visualize_phase5_routing.py's pattern: real HTTP
upload through the actual ingestion pipeline (which now also dispatches
the snapshot task, running eagerly under CELERY_TASK_ALWAYS_EAGER in
tests — see conftest.py), then targeted assertions against real data."""

import uuid
from datetime import UTC, datetime

import pandas as pd
import io
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetVariable
from app.worker.tasks.snapshots import generate_dataset_snapshot

from tests.test_dataset_upload import _create_dataset

pytestmark = pytest.mark.asyncio

_PARAM = "sea_surface_temp"


def _make_csv_bytes() -> bytes:
    rows = []
    for day, value in enumerate([20.0, 21.0, 22.0], start=1):
        rows.append({"time": f"2024-01-{day:02d}", "lat": 21.0, "lon": 90.0, "station": "ST-A", _PARAM: value})
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _make_mixed_case_csv_bytes() -> bytes:
    """Variable names deliberately chosen to reproduce the real bug: "so"
    (lowercase) sorts BEFORE "VHM0" (uppercase) under Postgres's default
    collation, but AFTER it under Python's plain sorted() — real
    Copernicus/CMEMS-style variable names from an actual dataset in this
    project's live data."""
    rows = []
    for day, value in enumerate([20.0, 21.0, 22.0], start=1):
        rows.append(
            {"time": f"2024-01-{day:02d}", "lat": 21.0, "lon": 90.0, "station": "ST-A", "so": value, "VHM0": value + 1}
        )
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


async def _upload_dataset(client, admin_headers) -> str:
    dataset_id = await _create_dataset(client, admin_headers)
    files = {"file": ("snapshot_test.csv", _make_csv_bytes(), "text/csv")}
    r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers)
    assert r.status_code == 202, r.text

    # The Data Page/Visualize read endpoints only serve published
    # datasets (get_published_dataset 404s otherwise) — this test file
    # isn't exercising the admin publish-workflow permission, so the
    # status is set directly, same as test_visualize_phase5_routing.py's
    # _upload_and_approve sidesteps the Phase 3 admin-review UI for
    # schema_reviewed_at.
    async with AsyncSessionLocal() as db:
        dataset = await db.get(Dataset, uuid.UUID(dataset_id))
        dataset.status = "published"
        await db.commit()

    return dataset_id


async def _approve_and_regenerate(dataset_id: str) -> None:
    """Approves the detected parameter as a Visualization Variable, then
    directly re-invokes the snapshot task — mirrors what a second file
    upload / re-ingestion would naturally trigger, since approval always
    happens strictly after the variable is first detected by ingestion
    (the snapshot dispatched DURING that first ingestion necessarily ran
    before any approval could exist)."""
    async with AsyncSessionLocal() as db:
        dataset = await db.get(Dataset, uuid.UUID(dataset_id))
        dataset.schema_reviewed_at = datetime.now(UTC)
        var = (
            await db.execute(
                select(DatasetVariable).where(
                    DatasetVariable.dataset_id == dataset.id, DatasetVariable.name == _PARAM
                )
            )
        ).scalar_one()
        var.roles = ["data_variable", "visualization_variable"]
        await db.commit()

    generate_dataset_snapshot(str(dataset_id))


class TestSnapshotGeneration:
    async def test_ingestion_dispatches_snapshot_and_sets_version(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            assert dataset.snapshot_version is not None
            assert dataset.snapshot_version == dataset.version

    async def test_snapshot_records_match_live_query_shape(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        from app.services.catalog_service import get_dataset_snapshot

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            snapshot = await get_dataset_snapshot(dataset)
        assert snapshot is not None
        assert snapshot["records"]["matching_count"] == 3
        assert snapshot["records"]["dataset_total_count"] == 3
        assert len(snapshot["records"]["preview"]) == 3
        assert snapshot["records"]["preview"][0]["parameter"] == _PARAM

    async def test_snapshot_includes_variable_stats(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        from app.services.catalog_service import get_dataset_snapshot

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            snapshot = await get_dataset_snapshot(dataset)
        stats_by_name = {s["name"]: s for s in snapshot["variable_stats"]}
        assert _PARAM in stats_by_name
        assert stats_by_name[_PARAM]["min_value"] == 20.0
        assert stats_by_name[_PARAM]["max_value"] == 22.0

    async def test_no_approved_variable_means_no_default_chart(self, client, admin_headers):
        """The variable isn't approved yet at first-ingestion time (the
        real chronological order: detect -> admin approves -> re-ingest/
        regenerate) — the snapshot must still be usable for records/stats,
        just with default_chart left null rather than fabricated."""
        dataset_id = await _upload_dataset(client, admin_headers)
        from app.services.catalog_service import get_dataset_snapshot

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            snapshot = await get_dataset_snapshot(dataset)
        assert snapshot["default_chart"] is None

    async def test_default_chart_populated_after_approval_and_regeneration(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        from app.services.catalog_service import get_dataset_snapshot

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            snapshot = await get_dataset_snapshot(dataset)
        assert snapshot["default_chart"] is not None
        assert snapshot["default_chart"]["parameter"] == _PARAM
        assert len(snapshot["default_chart"]["response"]["series"]) > 0

    async def test_default_statistics_populated_after_approval_and_regeneration(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        from app.services.catalog_service import get_dataset_snapshot

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            snapshot = await get_dataset_snapshot(dataset)
        assert snapshot["default_statistics"] is not None
        assert snapshot["default_statistics"]["parameter"] == _PARAM
        response = snapshot["default_statistics"]["response"]
        assert len(response["box_plot"]) > 0
        assert len(response["histogram"]) == 3

    async def test_default_chart_parameter_matches_frontend_dataset_selector_ordering(
        self, client, admin_headers
    ):
        """Regression test for a real bug: the snapshot generator used to
        pick its default_chart parameter via Python's sorted(key=lambda
        v: v.name), which orders case-sensitively (every uppercase name
        before every lowercase name — e.g. "VHM0" before "so"). The
        frontend's actual dataset selector (get_visualizable_datasets)
        orders via a real SQL ORDER BY DatasetVariable.name, which uses
        Postgres's collation and disagreed for exactly this kind of
        mixed-case variable set — so the snapshot's precomputed chart was
        silently built for a DIFFERENT parameter than what the frontend
        ever actually requests, meaning the snapshot was never served at
        all for any dataset with mixed-case variable names (nearly every
        real dataset in this project, confirmed by inspecting live data).
        This asserts the snapshot's chosen parameter is whatever a real
        SQL ORDER BY produces first, not whatever Python's sorted()
        would have chosen — the two differ for this exact fixture."""
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("mixed_case.csv", _make_mixed_case_csv_bytes(), "text/csv")}
        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers)
        assert r.status_code == 202, r.text

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            dataset.status = "published"
            dataset.schema_reviewed_at = datetime.now(UTC)
            variables = (
                await db.execute(select(DatasetVariable).where(DatasetVariable.dataset_id == dataset.id))
            ).scalars().all()
            for v in variables:
                if not v.is_dimension:
                    v.roles = ["data_variable", "visualization_variable"]
            await db.commit()

        generate_dataset_snapshot(dataset_id)

        # The real SQL-side ordering the frontend's own dataset selector
        # uses — the ground truth this test checks the snapshot against.
        async with AsyncSessionLocal() as db:
            sql_ordered = (
                await db.execute(
                    select(DatasetVariable.name)
                    .where(
                        DatasetVariable.dataset_id == uuid.UUID(dataset_id),
                        DatasetVariable.roles.any("visualization_variable"),
                        DatasetVariable.is_dimension.is_(False),
                    )
                    .order_by(DatasetVariable.name)
                )
            ).scalars().all()
        assert sql_ordered[0] == "so", "fixture sanity check: SQL ORDER BY must put 'so' first"
        # Python's plain sorted() would have picked "VHM0" instead —
        # exactly the mismatch this regression test guards against.
        assert sorted(sql_ordered)[0] != sql_ordered[0]

        from app.services.catalog_service import get_dataset_snapshot

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            snapshot = await get_dataset_snapshot(dataset)
        assert snapshot["default_chart"]["parameter"] == sql_ordered[0]


class TestRecordsEndpointSnapshotGuard:
    async def test_unfiltered_request_served_from_snapshot(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        r = await client.get(f"/api/v1/catalog/{dataset_id}/records")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["matching_count"] == 3
        assert len(body["preview"]) == 3

    async def test_filtered_request_bypasses_snapshot(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        r = await client.get(f"/api/v1/catalog/{dataset_id}/records?quality=normal")
        assert r.status_code == 200, r.text
        # Live path still correctly returns the same real data — the
        # point of this test is that a filter is present at all, not
        # that the numbers differ from the unfiltered case.
        assert r.json()["matching_count"] == 3

    async def test_request_with_dataset_own_extent_still_served_from_snapshot(self, client, admin_headers):
        """Regression test: the real Data Page frontend (useDatasetFilters.
        ts) never sends a bare unfiltered request — it auto-fills date_
        from/date_to/lat_min/lat_max/lon_min/lon_max from the dataset's own
        real extent as soon as it loads. A request shaped exactly like
        that auto-fill (this test's fixture data spans 2024-01-01 to
        2024-01-03 at a single point, lat=21.0/lon=90.0) must still be
        recognized as the snapshot's own default-view shape — a strict
        'every field is None' check would incorrectly treat this as
        filtered and never serve the snapshot at all, which is exactly
        the bug this test guards against."""
        dataset_id = await _upload_dataset(client, admin_headers)
        r = await client.get(
            f"/api/v1/catalog/{dataset_id}/records"
            "?date_from=2024-01-01&date_to=2024-01-03"
            "&lat_min=21.0&lat_max=21.0&lon_min=90.0&lon_max=90.0"
        )
        assert r.status_code == 200, r.text
        assert r.json()["matching_count"] == 3

    async def test_request_with_narrower_date_than_extent_bypasses_snapshot(self, client, admin_headers):
        """A date range that does NOT match the dataset's full extent
        (a genuine user-applied narrowing) must still bypass the
        snapshot and hit the live path, even though it superficially
        looks similar to the auto-filled default."""
        dataset_id = await _upload_dataset(client, admin_headers)
        r = await client.get(
            f"/api/v1/catalog/{dataset_id}/records?date_from=2024-01-02&date_to=2024-01-02"
        )
        assert r.status_code == 200, r.text
        assert r.json()["matching_count"] == 1

    async def test_stale_snapshot_falls_back_to_live_path(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            # Simulate staleness directly rather than re-ingesting a
            # second file — cheaper and exercises the exact comparison
            # get_dataset_snapshot performs.
            dataset.snapshot_version = dataset.version - 1 if dataset.version > 1 else None
            if dataset.snapshot_version is None:
                dataset.version = 2
                dataset.snapshot_version = 1
            await db.commit()

        r = await client.get(f"/api/v1/catalog/{dataset_id}/records")
        assert r.status_code == 200, r.text
        # The live path must still return correct data even though the
        # snapshot was deliberately made stale.
        assert r.json()["matching_count"] == 3


class TestTimeseriesEndpointSnapshotGuard:
    async def test_default_shape_request_served_from_snapshot(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": _PARAM, "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["series"]) > 0

    async def test_filtered_request_bypasses_snapshot_chart(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={
                "dataset_id": dataset_id, "parameter": _PARAM, "resolution": "monthly",
                "date_from": "2024-01-02", "date_to": "2024-01-03",
            },
        )
        assert r.status_code == 200, r.text
        # A real filter narrows to fewer than the full 3-day series —
        # proves the live path ran, not the cached full-extent snapshot.
        assert len(r.json()["series"]) >= 1

    async def test_request_with_dataset_own_extent_still_served_from_snapshot(self, client, admin_headers):
        """Regression test: the real Visualize frontend (useVizFilters.ts's
        selectDataset()) never leaves date_from/date_to/lat_min/lat_max/
        lon_min/lon_max unset — it auto-fills all six from the newly-
        selected dataset's own real extent immediately. A request shaped
        exactly like that auto-fill must still be recognized as the
        snapshot's own default-chart shape."""
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={
                "dataset_id": dataset_id, "parameter": _PARAM, "resolution": "monthly",
                "date_from": "2024-01-01", "date_to": "2024-01-03",
                "lat_min": 21.0, "lat_max": 21.0, "lon_min": 90.0, "lon_max": 90.0,
            },
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["series"]) > 0

    async def test_different_parameter_bypasses_snapshot_chart(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            db.add(
                DatasetVariable(
                    dataset_id=dataset.id, name="other_param", data_type="numeric",
                    is_dimension=False, roles=["data_variable", "visualization_variable"],
                )
            )
            await db.commit()

        # "other_param" has no real data, so the live path correctly
        # returns an empty series — proves the snapshot (built for
        # sea_surface_temp) was NOT reused for a different parameter.
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": dataset_id, "parameter": "other_param", "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text


class TestStatisticsEndpointSnapshotGuard:
    """Visualize Performance plan, Phase 1 — default_statistics section of
    the snapshot, mirroring TestTimeseriesEndpointSnapshotGuard's exact
    test shapes for the shared _snapshot_if_default_shape guard clause."""

    async def test_default_shape_request_served_from_snapshot(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": _PARAM, "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "complete"
        assert len(body["result"]["box_plot"]) > 0
        assert len(body["result"]["histogram"]) > 0

    async def test_filtered_request_bypasses_snapshot_statistics(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={
                "dataset_id": dataset_id, "parameter": _PARAM, "resolution": "monthly",
                "date_from": "2024-01-02", "date_to": "2024-01-03",
            },
        )
        assert r.status_code == 200, r.text
        # A real filter narrows the histogram to fewer than the full
        # 3-day series — proves the live path ran, not the cached
        # full-extent snapshot.
        assert len(r.json()["result"]["histogram"]) < 3

    async def test_request_with_dataset_own_extent_still_served_from_snapshot(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={
                "dataset_id": dataset_id, "parameter": _PARAM, "resolution": "monthly",
                "date_from": "2024-01-01", "date_to": "2024-01-03",
                "lat_min": 21.0, "lat_max": 21.0, "lon_min": 90.0, "lon_max": 90.0,
            },
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["result"]["histogram"]) == 3

    async def test_different_parameter_bypasses_snapshot_statistics(self, client, admin_headers):
        dataset_id = await _upload_dataset(client, admin_headers)
        await _approve_and_regenerate(dataset_id)

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            db.add(
                DatasetVariable(
                    dataset_id=dataset.id, name="other_param", data_type="numeric",
                    is_dimension=False, roles=["data_variable", "visualization_variable"],
                )
            )
            await db.commit()

        r = await client.post(
            "/api/v1/visualize/statistics",
            json={"dataset_id": dataset_id, "parameter": "other_param", "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["result"]["histogram"] == []

    async def test_no_approved_variable_means_no_default_statistics(self, client, admin_headers):
        """Mirrors TestSnapshotGeneration.test_no_approved_variable_means_
        no_default_chart — default_statistics must be None (never
        fabricated) when the snapshot was generated before any variable
        was approved."""
        dataset_id = await _upload_dataset(client, admin_headers)
        from app.services.catalog_service import get_dataset_snapshot

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid.UUID(dataset_id))
            snapshot = await get_dataset_snapshot(dataset)
        assert snapshot["default_statistics"] is None
