import uuid

import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models.catalog import DatasetRecord
from app.scripts.backfill_dataset_records import backfill
from tests.test_dataset_upload import _create_dataset, _make_csv_bytes

pytestmark = pytest.mark.asyncio


async def _upload_and_strip_records(client, admin_headers) -> tuple[str, str]:
    """Uploads a real CSV through the full HTTP pipeline (so DatasetRecord
    rows genuinely get written by the ingestion fix), then deletes those
    rows directly — simulating the exact state every dataset uploaded
    before this fix is actually in: a successfully-ingested DatasetFile
    (file_metadata set, dataset.record_count correct) with zero
    DatasetRecord rows."""
    dataset_id = await _create_dataset(client, admin_headers)
    files = {"file": ("backfill_test.csv", _make_csv_bytes(), "text/csv")}
    r = await client.post(
        f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
    )
    assert r.status_code == 202, r.text
    dataset_file_id = r.json()["dataset_file"]["id"]

    async with AsyncSessionLocal() as db:
        await db.execute(delete(DatasetRecord).where(DatasetRecord.dataset_id == uuid.UUID(dataset_id)))
        await db.commit()

        result = await db.execute(
            select(DatasetRecord).where(DatasetRecord.dataset_id == uuid.UUID(dataset_id))
        )
        assert result.scalars().all() == []

    return dataset_id, dataset_file_id


class TestBackfillDatasetRecords:
    async def test_dry_run_lists_eligible_files_without_writing(self, client, admin_headers):
        dataset_id, dataset_file_id = await _upload_and_strip_records(client, admin_headers)

        plan = await backfill(dataset_id=uuid.UUID(dataset_id), dry_run=True)
        assert plan["dry_run"] is True
        assert plan["eligible_count"] == 1
        assert plan["files"][0]["dataset_file_id"] == dataset_file_id

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetRecord).where(DatasetRecord.dataset_id == uuid.UUID(dataset_id))
            )
            assert result.scalars().all() == []

    async def test_backfill_restores_records_matching_original_upload(self, client, admin_headers):
        dataset_id, _ = await _upload_and_strip_records(client, admin_headers)

        result = await backfill(dataset_id=uuid.UUID(dataset_id), dry_run=False)
        assert result["dry_run"] is False
        assert result["eligible_count"] == 1
        written_counts = list(result["records_written"].values())
        assert written_counts == [15]

        async with AsyncSessionLocal() as db:
            records = (
                await db.execute(
                    select(DatasetRecord).where(DatasetRecord.dataset_id == uuid.UUID(dataset_id))
                )
            ).scalars().all()
        assert len(records) == 15
        assert all(rec.parameter == "sea_surface_temp" for rec in records)

    async def test_dataset_with_existing_records_is_not_touched(self, client, admin_headers):
        """A dataset that already has records (e.g. was never affected by
        the bug, or was already backfilled) must be left alone — this tool
        only targets datasets stuck at zero."""
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("already_fine.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202

        plan = await backfill(dataset_id=uuid.UUID(dataset_id), dry_run=True)
        assert plan["eligible_count"] == 0

    async def test_geotiff_dataset_is_never_eligible(self, client, admin_headers, tmp_path):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        dataset_id = await _create_dataset(client, admin_headers)
        p = tmp_path / "backfill_raster.tif"
        width, height = 50, 40
        rng = np.random.default_rng(3)
        data = (rng.random((height, width)) * 30).astype("float32")
        transform = from_origin(88.0, 26.7, 0.01, 0.01)
        with rasterio.open(
            p, "w", driver="GTiff", height=height, width=width, count=1,
            dtype="float32", crs="EPSG:4326", transform=transform,
        ) as dst:
            dst.write(data, 1)
        files = {"file": ("backfill_raster.tif", p.read_bytes(), "image/tiff")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202

        plan = await backfill(dataset_id=uuid.UUID(dataset_id), dry_run=True)
        assert plan["eligible_count"] == 0
