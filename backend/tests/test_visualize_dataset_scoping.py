import uuid
from datetime import UTC, date, datetime

import pytest

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetRecord, DatasetStatus, DatasetVariable

pytestmark = pytest.mark.asyncio

_PARAM = "shared_param"


async def _seed_two_reviewed_datasets_sharing_a_parameter_name() -> dict:
    """Two independently-reviewed datasets that happen to detect a
    variable with the same name — proves dataset_id scoping genuinely
    isolates them rather than accidentally aggregating across datasets."""
    async with AsyncSessionLocal() as db:
        ds_a = Dataset(
            code="BD-VIZSCOPE-A",
            title="Viz Scoping Test A",
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
            schema_reviewed_at=datetime.now(UTC),
        )
        ds_b = Dataset(
            code="BD-VIZSCOPE-B",
            title="Viz Scoping Test B",
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
            schema_reviewed_at=datetime.now(UTC),
        )
        db.add_all([ds_a, ds_b])
        await db.flush()

        db.add_all(
            [
                DatasetVariable(
                    dataset_id=ds_a.id,
                    name=_PARAM,
                    data_type="numeric",
                    is_dimension=False,
                    roles=["visualization_variable"],
                ),
                DatasetVariable(
                    dataset_id=ds_b.id,
                    name=_PARAM,
                    data_type="numeric",
                    is_dimension=False,
                    roles=["visualization_variable"],
                ),
                # Detected on B but never approved — must never validate.
                DatasetVariable(
                    dataset_id=ds_b.id,
                    name="unapproved_param",
                    data_type="numeric",
                    is_dimension=False,
                    roles=[],
                ),
            ]
        )
        await db.flush()

        for month in range(1, 4):
            db.add(
                DatasetRecord(
                    dataset_id=ds_a.id,
                    time=date(2024, month, 15),
                    lat=21.0,
                    lon=90.0,
                    parameter=_PARAM,
                    value=100.0 + month,
                    geom="SRID=4326;POINT(90.0 21.0)",
                )
            )
            db.add(
                DatasetRecord(
                    dataset_id=ds_b.id,
                    time=date(2024, month, 15),
                    lat=22.0,
                    lon=91.0,
                    parameter=_PARAM,
                    value=200.0 + month,
                    geom="SRID=4326;POINT(91.0 22.0)",
                )
            )
        await db.commit()
        return {"a": str(ds_a.id), "b": str(ds_b.id)}


async def _seed_unreviewed_dataset_with_viz_variable() -> str:
    async with AsyncSessionLocal() as db:
        dataset = Dataset(
            code="BD-VIZSCOPE-C",
            title="Viz Scoping Test C — Unreviewed",
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.flush()
        db.add(
            DatasetVariable(
                dataset_id=dataset.id,
                name="never_listed_param",
                data_type="numeric",
                is_dimension=False,
                roles=["visualization_variable"],
            )
        )
        await db.commit()
        return str(dataset.id)


class TestVisualizableDatasetsEndpoint:
    async def test_lists_only_reviewed_datasets_with_approved_viz_variable(self, client):
        ids = await _seed_two_reviewed_datasets_sharing_a_parameter_name()
        await _seed_unreviewed_dataset_with_viz_variable()

        r = await client.get("/api/v1/visualize/datasets")
        assert r.status_code == 200, r.text
        body = r.json()
        listed_ids = {row["id"] for row in body}

        assert ids["a"] in listed_ids
        assert ids["b"] in listed_ids

        by_id = {row["id"]: row for row in body}
        assert by_id[ids["a"]]["variables"] == [_PARAM]
        # unapproved_param never appears even though it was detected on B.
        assert by_id[ids["b"]]["variables"] == [_PARAM]

    async def test_unreviewed_dataset_never_listed(self, client):
        unreviewed_id = await _seed_unreviewed_dataset_with_viz_variable()
        r = await client.get("/api/v1/visualize/datasets")
        listed_ids = {row["id"] for row in r.json()}
        assert unreviewed_id not in listed_ids


class TestParameterValidation:
    async def test_unapproved_parameter_rejected_with_422(self, client):
        ids = await _seed_two_reviewed_datasets_sharing_a_parameter_name()
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={
                "dataset_id": ids["b"],
                "parameter": "unapproved_param",
                "date_from": "2024-01-01",
                "date_to": "2024-12-31",
            },
        )
        assert r.status_code == 422, r.text

    async def test_nonexistent_parameter_rejected_with_422(self, client):
        ids = await _seed_two_reviewed_datasets_sharing_a_parameter_name()
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": ids["a"], "parameter": "totally_made_up"},
        )
        assert r.status_code == 422, r.text

    async def test_approved_parameter_with_dataset_id_scopes_results_correctly(self, client):
        ids = await _seed_two_reviewed_datasets_sharing_a_parameter_name()
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"dataset_id": ids["a"], "parameter": _PARAM, "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        values = [p["value"] for p in r.json()["series"]]
        # Dataset A's values are 101/102/103 — dataset B's 201/202/203 must
        # never leak in despite sharing the same parameter name.
        assert all(100 <= v < 200 for v in values), values

    async def test_omitting_dataset_id_preserves_cross_dataset_behavior(self, client):
        """Regression guard for the fallback: a request with no dataset_id
        must behave exactly like before this phase — no validation, and
        results aggregate across every dataset sharing the parameter."""
        ids = await _seed_two_reviewed_datasets_sharing_a_parameter_name()
        r = await client.post(
            "/api/v1/visualize/timeseries",
            json={"parameter": _PARAM, "resolution": "monthly"},
        )
        assert r.status_code == 200, r.text
        values = [p["value"] for p in r.json()["series"]]
        # Cross-dataset average of (101,201)/(102,202)/(103,203) per month.
        assert any(v >= 150 for v in values), values
