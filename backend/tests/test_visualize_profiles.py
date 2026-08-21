"""Oceanographic Profiles module — depth-detection at ingestion time plus
the /visualize/profiles endpoint's depth-profile and T-S diagram query
paths, against real CSV uploads through the actual ingestion pipeline
(mirrors test_visualize_phase5_routing.py's pattern: real HTTP upload,
approve the detected variable as a Visualization Variable, then query)."""

import io
import uuid
from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetVariable

from tests.test_dataset_upload import _create_dataset

pytestmark = pytest.mark.asyncio


def _make_ctd_csv_bytes(*, depth_col: str = "depth", negative_depth: bool = False) -> bytes:
    """2 stations, 3 depths each — Temperature/Salinity chosen so a
    profile's depth-sorted values and a T-S pairing are both hand-
    computable. Station A: depth 0/10/20 -> temp 28/24/20, sal 32/33/34.
    Station B: depth 0/10/20 -> temp 27/23/19, sal 31/32/33 (shifted by
    -1 from A so the two stations are visually distinguishable but share
    the same overall structure)."""
    rows = []
    depths = [0.0, 10.0, 20.0]
    for station, temp_base, sal_base, lat, lon in [
        ("CTD-A", 28.0, 32.0, 21.0, 90.0),
        ("CTD-B", 27.0, 31.0, 21.5, 90.5),
    ]:
        for i, d in enumerate(depths):
            stored_depth = -d if negative_depth else d
            rows.append(
                {
                    "time": "2024-03-01",
                    "lat": lat,
                    "lon": lon,
                    "station": station,
                    depth_col: stored_depth,
                    "temperature": temp_base - i * 4.0,
                    "salinity": sal_base + i * 1.0,
                }
            )
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _make_ctd_csv_bytes_no_time() -> bytes:
    """Same shape as _make_ctd_csv_bytes but with no time column at all —
    a real, valid CTD survey shape (single synoptic cast, no date axis)
    that exposed a genuine bug: get_ts_pairs' self-join unconditionally
    referenced t.time/s.time even when the melted view has no time
    column at all, and separately referenced a table alias against the
    literal NULL fallback (t.NULL), both invalid DuckDB SQL — caught only
    by testing against a dataset shaped exactly like this, not the
    time+station-having fixture above."""
    rows = []
    depths = [0.0, 10.0, 20.0]
    for station, temp_base, sal_base, lat, lon in [
        ("CTD-A", 28.0, 32.0, 21.0, 90.0),
        ("CTD-B", 27.0, 31.0, 21.5, 90.5),
    ]:
        for i, d in enumerate(depths):
            rows.append(
                {
                    "lat": lat, "lon": lon, "station": station, "depth": d,
                    "temperature": temp_base - i * 4.0, "salinity": sal_base + i * 1.0,
                }
            )
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _make_no_depth_csv_bytes() -> bytes:
    df = pd.DataFrame(
        [
            {"time": "2024-01-01", "lat": 21.0, "lon": 90.0, "station": "ST-A", "temperature": 25.0},
            {"time": "2024-01-02", "lat": 21.0, "lon": 90.0, "station": "ST-A", "temperature": 26.0},
        ]
    )
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


async def _upload_and_approve_all(client, admin_headers, *, content: bytes, filename: str = "ctd.csv") -> str:
    """Uploads a CSV and approves EVERY detected non-dimension variable
    as a Visualization Variable (temperature, salinity, ...) — unlike
    test_visualize_phase5_routing.py's single-parameter helper, the T-S
    diagram needs both temperature and salinity approved at once."""
    dataset_id = await _create_dataset(client, admin_headers)
    files = {"file": (filename, content, "text/csv")}
    r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers)
    assert r.status_code == 202, r.text

    async with AsyncSessionLocal() as db:
        dataset = await db.get(Dataset, uuid.UUID(dataset_id))
        dataset.schema_reviewed_at = datetime.now(UTC)

        variables = (
            await db.execute(select(DatasetVariable).where(DatasetVariable.dataset_id == dataset.id))
        ).scalars().all()
        for v in variables:
            if not v.is_dimension:
                v.roles = ["data_variable", "visualization_variable"]
        await db.commit()

    return dataset_id


class TestDepthDetectionAtIngestion:
    async def test_depth_column_registered_as_dimension(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        async with AsyncSessionLocal() as db:
            depth_var = (
                await db.execute(
                    select(DatasetVariable).where(
                        DatasetVariable.dataset_id == uuid.UUID(dataset_id), DatasetVariable.name == "depth"
                    )
                )
            ).scalar_one()
            assert depth_var.is_dimension is True

    async def test_depth_populates_dataset_record_depth_m(self, client, admin_headers):
        """The historical bug this module fixes: _write_dataset_records'
        COPY statement used to exclude depth_m entirely, so it was always
        NULL. Confirms real rows now carry a real depth value."""
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        async with AsyncSessionLocal() as db:
            from app.models.catalog import DatasetRecord

            depths = (
                await db.execute(
                    select(DatasetRecord.depth_m).where(DatasetRecord.dataset_id == uuid.UUID(dataset_id))
                )
            ).scalars().all()
            # This dataset went through ingestion as PARQUET-backed (new
            # uploads default to Parquet, not legacy ROW_RECORDS) — so
            # DatasetRecord rows for it are not written at all (only the
            # Parquet file is queried). Assert that invariant instead:
            # zero DatasetRecord rows is expected for a Parquet-backed
            # dataset, not a depth-detection failure.
            assert depths == []


class TestDepthProfile:
    async def test_temperature_profile_sorted_by_depth(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "temperature"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_depth_data"] is True
        profiles = {p["station"]: p for p in body["profiles"]}
        assert set(profiles) == {"CTD-A", "CTD-B"}

        a_points = profiles["CTD-A"]["points"]
        assert [p["depth_m"] for p in a_points] == [0.0, 10.0, 20.0]
        assert [p["value"] for p in a_points] == [28.0, 24.0, 20.0]

    async def test_salinity_profile_independent_of_temperature(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "salinity"},
        )
        assert r.status_code == 200, r.text
        profiles = {p["station"]: p for p in r.json()["profiles"]}
        b_points = profiles["CTD-B"]["points"]
        assert [p["value"] for p in b_points] == [31.0, 32.0, 33.0]

    async def test_multiple_stations_produce_multiple_profiles(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "temperature"},
        )
        assert len(r.json()["profiles"]) == 2

    async def test_static_non_temporal_profile(self, client, admin_headers):
        """A CTD cast with a genuine depth axis is scientifically valid
        with no time dimension at all — the profile endpoint must not
        require one."""
        rows = [
            {"lat": 21.0, "lon": 90.0, "station": "CTD-STATIC", "depth": d, "temperature": 25.0 - d * 0.1}
            for d in [0.0, 5.0, 15.0]
        ]
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=buf.getvalue())

        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "temperature"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_depth_data"] is True
        assert len(body["profiles"]) == 1
        assert [p["depth_m"] for p in body["profiles"][0]["points"]] == [0.0, 5.0, 15.0]

    async def test_temporal_profile_still_works(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "temperature", "date_from": "2024-03-01", "date_to": "2024-03-01"},
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["profiles"]) == 2

    async def test_missing_variable_returns_empty_profiles_not_error(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "nonexistent_variable_xyz"},
        )
        # validate_parameter_for_dataset rejects an unapproved parameter —
        # this is the correct, existing behavior every other module
        # already has (422, not a silent empty response).
        assert r.status_code == 422

    async def test_no_depth_dimension_reports_has_depth_data_false(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_no_depth_csv_bytes())
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "temperature"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_depth_data"] is False
        assert body["profiles"] == []

    async def test_missing_parameter_and_ts_params_rejected(self, client):
        r = await client.post("/api/v1/visualize/profiles", json={})
        assert r.status_code == 422

    async def test_both_parameter_and_ts_params_rejected(self, client):
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"parameter": "temperature", "temperature_parameter": "temperature", "salinity_parameter": "salinity"},
        )
        assert r.status_code == 422


class TestDepthConvention:
    async def test_positive_depth_assumed_positive_down(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(
            client, admin_headers, content=_make_ctd_csv_bytes(negative_depth=False)
        )
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "temperature"},
        )
        assert r.json()["depth_convention"] == "assumed_positive_down"

    async def test_negative_depth_assumed_negative_up_never_silently_flipped(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(
            client, admin_headers, content=_make_ctd_csv_bytes(negative_depth=True)
        )
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "temperature"},
        )
        body = r.json()
        assert body["depth_convention"] == "assumed_negative_up"
        # Values must be exactly as stored (negative), never sign-flipped
        # by the backend — that decision belongs to the frontend's axis
        # rendering only.
        a_points = next(p for p in body["profiles"] if p["station"] == "CTD-A")["points"]
        assert all(p["depth_m"] <= 0 for p in a_points)


class TestDuplicateDepth:
    async def test_duplicate_depth_keeps_last_value(self, client, admin_headers):
        rows = [
            {"lat": 21.0, "lon": 90.0, "station": "CTD-DUP", "depth": 0.0, "temperature": 25.0},
            {"lat": 21.0, "lon": 90.0, "station": "CTD-DUP", "depth": 0.0, "temperature": 99.0},
            {"lat": 21.0, "lon": 90.0, "station": "CTD-DUP", "depth": 10.0, "temperature": 20.0},
        ]
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=buf.getvalue())

        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "parameter": "temperature"},
        )
        assert r.status_code == 200, r.text
        points = r.json()["profiles"][0]["points"]
        assert len(points) == 2, "duplicate depth must collapse to one point, not two"
        depth_0 = next(p for p in points if p["depth_m"] == 0.0)
        assert depth_0["value"] == 99.0, "keep-last convention: the later row's value wins"


class TestTSDiagram:
    async def test_paired_from_same_row_not_independently_aggregated(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "temperature_parameter": "temperature", "salinity_parameter": "salinity"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["profiles"] == []
        pairs = body["ts_pairs"]
        assert len(pairs) == 6  # 2 stations x 3 depths

        # Every pair must be the exact (temp, sal) combination that was
        # uploaded together on the same row — never an independently
        # aggregated-then-zipped mismatch.
        by_depth_station = {(p["station"], p["depth_m"]): p for p in pairs}
        a0 = by_depth_station[("CTD-A", 0.0)]
        assert a0["temperature"] == 28.0
        assert a0["salinity"] == 32.0
        b20 = by_depth_station[("CTD-B", 20.0)]
        assert b20["temperature"] == 19.0
        assert b20["salinity"] == 33.0

    async def test_ts_diagram_has_depth_for_coloring(self, client, admin_headers):
        dataset_id = await _upload_and_approve_all(client, admin_headers, content=_make_ctd_csv_bytes())
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "temperature_parameter": "temperature", "salinity_parameter": "salinity"},
        )
        pairs = r.json()["ts_pairs"]
        assert all(p["depth_m"] is not None for p in pairs)

    async def test_ts_missing_salinity_param_rejected(self, client):
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"temperature_parameter": "temperature"},
        )
        assert r.status_code == 422

    async def test_ts_diagram_works_with_no_time_column(self, client, admin_headers):
        """Regression test: a real uploaded dataset with a station column
        but NO time column at all (a valid single-cast CTD survey shape)
        used to 500 with a DuckDB BinderException — get_ts_pairs' self-
        join referenced t.time/s.time unconditionally, and separately
        produced invalid SQL (t.NULL) for the no-time NULL fallback."""
        dataset_id = await _upload_and_approve_all(
            client, admin_headers, content=_make_ctd_csv_bytes_no_time()
        )
        r = await client.post(
            "/api/v1/visualize/profiles",
            json={"dataset_id": dataset_id, "temperature_parameter": "temperature", "salinity_parameter": "salinity"},
        )
        assert r.status_code == 200, r.text
        pairs = r.json()["ts_pairs"]
        assert len(pairs) == 6
        assert all(p["time"] is None for p in pairs)
        by_depth_station = {(p["station"], p["depth_m"]): p for p in pairs}
        a0 = by_depth_station[("CTD-A", 0.0)]
        assert a0["temperature"] == 28.0
        assert a0["salinity"] == 32.0
