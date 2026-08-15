import uuid
from datetime import UTC, date, datetime

import pytest

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetRecord, DatasetStatus, DatasetVariable

pytestmark = pytest.mark.asyncio


async def _seed_reviewed_dataset_lat_lon_only() -> str:
    """A published, reviewed dataset with only lat/lon approved as
    dimensions — no time, no depth — proving the schema endpoint reflects
    exactly what was approved, not a fixed field list."""
    async with AsyncSessionLocal() as db:
        dataset = Dataset(
            code="BD-SCHEMA-001",
            title="Schema Filters Test — Lat/Lon Only",
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
            schema_reviewed_at=datetime.now(UTC),
        )
        db.add(dataset)
        await db.flush()

        db.add_all(
            [
                DatasetVariable(
                    dataset_id=dataset.id,
                    name="lat",
                    data_type="numeric",
                    is_dimension=True,
                    roles=["dimension"],
                ),
                DatasetVariable(
                    dataset_id=dataset.id,
                    name="lon",
                    data_type="numeric",
                    is_dimension=True,
                    roles=["dimension"],
                ),
                DatasetVariable(
                    dataset_id=dataset.id,
                    name="salinity",
                    data_type="numeric",
                    is_dimension=False,
                    roles=["data_variable"],
                    min_value=30.0,
                    max_value=36.0,
                ),
                # Detected by Phase 2 but never assigned a role — must not
                # appear in the schema response even though the dataset
                # itself is reviewed.
                DatasetVariable(
                    dataset_id=dataset.id,
                    name="unassigned_var",
                    data_type="text",
                    is_dimension=False,
                    roles=[],
                ),
            ]
        )
        await db.commit()
        return str(dataset.id)


async def _seed_unreviewed_dataset() -> str:
    async with AsyncSessionLocal() as db:
        dataset = Dataset(
            code="BD-SCHEMA-002",
            title="Schema Filters Test — Unreviewed",
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.flush()
        db.add(
            DatasetVariable(
                dataset_id=dataset.id,
                name="time",
                data_type="temporal",
                is_dimension=True,
                roles=["dimension"],
            )
        )
        await db.commit()
        return str(dataset.id)


class TestDatasetSchemaEndpoint:
    async def test_reviewed_dataset_returns_only_approved_variables(self, client):
        dataset_id = await _seed_reviewed_dataset_lat_lon_only()
        r = await client.get(f"/api/v1/catalog/{dataset_id}/schema")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body is not None
        assert body["reviewed_at"] is not None

        names = {v["name"] for v in body["variables"]}
        assert names == {"lat", "lon", "salinity"}  # unassigned_var excluded

        by_name = {v["name"]: v for v in body["variables"]}
        assert by_name["lat"]["is_dimension"] is True
        assert by_name["lat"]["roles"] == ["dimension"]
        assert by_name["salinity"]["roles"] == ["data_variable"]
        assert by_name["salinity"]["min_value"] == 30.0

        # No time dimension was ever approved on this dataset.
        assert "time" not in names

    async def test_unreviewed_dataset_returns_null(self, client):
        dataset_id = await _seed_unreviewed_dataset()
        r = await client.get(f"/api/v1/catalog/{dataset_id}/schema")
        assert r.status_code == 200, r.text
        assert r.json() is None

    async def test_nonexistent_dataset_404s(self, client):
        r = await client.get(f"/api/v1/catalog/{uuid.uuid4()}/schema")
        assert r.status_code == 404

    async def test_draft_dataset_schema_404s(self, client):
        async with AsyncSessionLocal() as db:
            dataset = Dataset(
                code="BD-SCHEMA-003",
                title="Draft — never public",
                status=DatasetStatus.DRAFT.value,
                record_count=0,
                schema_reviewed_at=datetime.now(UTC),
            )
            db.add(dataset)
            await db.commit()
            dataset_id = str(dataset.id)

        r = await client.get(f"/api/v1/catalog/{dataset_id}/schema")
        assert r.status_code == 404


class TestRecordsFilteringRegression:
    """Filtering mechanics are unchanged by this phase — a reviewed
    dataset's /records endpoint must still filter exactly like before."""

    async def test_reviewed_dataset_records_filter_by_date_unaffected(self, client):
        dataset_id = await _seed_reviewed_dataset_lat_lon_only()
        async with AsyncSessionLocal() as db:
            db.add(
                DatasetRecord(
                    dataset_id=uuid.UUID(dataset_id),
                    time=date(2024, 6, 1),
                    lat=22.0,
                    lon=91.0,
                    parameter="salinity",
                    value=32.0,
                    geom="SRID=4326;POINT(91.0 22.0)",
                )
            )
            db.add(
                DatasetRecord(
                    dataset_id=uuid.UUID(dataset_id),
                    time=date(2023, 1, 1),
                    lat=22.0,
                    lon=91.0,
                    parameter="salinity",
                    value=31.0,
                    geom="SRID=4326;POINT(91.0 22.0)",
                )
            )
            await db.commit()

        r = await client.get(
            f"/api/v1/catalog/{dataset_id}/records", params={"date_from": "2024-01-01"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["matching_count"] == 1
        assert body["dataset_total_count"] == 2
