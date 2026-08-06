import io

import numpy as np
import pandas as pd
import pytest
import rasterio
import scipy.io
from rasterio.transform import from_origin

pytestmark = pytest.mark.asyncio


def _make_csv_bytes() -> bytes:
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=15, freq="D"),
            "lat": [22.0 + i * 0.05 for i in range(15)],
            "lon": [91.0 + i * 0.05 for i in range(15)],
            "sea_surface_temp": [27.0 + i * 0.1 for i in range(15)],
        }
    )
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _make_mat_bytes(tmp_path) -> bytes:
    p = tmp_path / "upload_test.mat"
    scipy.io.savemat(
        p,
        {
            "lat": np.array([22.0 + i * 0.05 for i in range(10)]),
            "lon": np.array([91.0 + i * 0.05 for i in range(10)]),
            "temperature": np.array([27.0 + i * 0.1 for i in range(10)]),
        },
    )
    return p.read_bytes()


def _make_geotiff_bytes(tmp_path) -> bytes:
    p = tmp_path / "upload_test.tif"
    width, height = 300, 200
    rng = np.random.default_rng(7)
    data = (rng.random((height, width)) * 30).astype("float32")
    transform = from_origin(88.0, 26.7, 0.01, 0.01)
    with rasterio.open(
        p, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(data, 1)
        dst.set_band_description(1, "sea_surface_temp")
    return p.read_bytes()


async def _create_dataset(client, admin_headers) -> str:
    r = await client.post(
        "/api/v1/admin/datasets",
        json={"title": "Upload Test Dataset", "description": "for integration tests"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestDatasetShellCreation:
    async def test_create_requires_permission(self, client):
        r = await client.post(
            "/api/v1/admin/datasets", json={"title": "No Auth", "description": None}
        )
        assert r.status_code == 401

    async def test_create_generates_code_when_omitted(self, client, admin_headers):
        r = await client.post(
            "/api/v1/admin/datasets",
            json={"title": "Auto Code Dataset", "description": None},
            headers=admin_headers,
        )
        assert r.status_code == 201
        body = r.json()
        assert body["code"].startswith("BD-")
        assert body["status"] == "draft"

    async def test_create_rejects_duplicate_code(self, client, admin_headers):
        payload = {"title": "Dup", "description": None, "code": "BD-DUPTEST"}
        r1 = await client.post("/api/v1/admin/datasets", json=payload, headers=admin_headers)
        assert r1.status_code == 201
        r2 = await client.post("/api/v1/admin/datasets", json=payload, headers=admin_headers)
        assert r2.status_code == 409


class TestCsvUpload:
    async def test_upload_csv_succeeds_and_processes(self, client, admin_headers):
        dataset_id = await _create_dataset(client, admin_headers)

        files = {"file": ("test_upload.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["dataset_file"]["file_format"] == "csv"
        assert body["dataset_file"]["checksum"] is not None
        upload_id = body["upload"]["id"]

        # Ingestion Celery task runs eagerly in tests (see conftest) — poll once.
        r = await client.get(f"/api/v1/admin/datasets/uploads/{upload_id}", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "complete"

    async def test_upload_populates_dataset_metadata(self, client, admin_headers):
        """There's no dataset-detail GET endpoint until Phase 3, so verify
        the auto-extracted metadata landed correctly by querying the DB
        directly — this is what Phase 3's catalog endpoints will read from."""
        import uuid as uuid_module

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import Dataset

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("meta_test.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202
        body = r.json()["dataset_file"]
        assert body["file_metadata"] is not None
        assert "sea_surface_temp" in body["file_metadata"]["variables"]
        assert body["temporal_start"] == "2024-01-01"
        assert body["temporal_end"] == "2024-01-15"

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid_module.UUID(dataset_id))
            assert dataset.record_count == 15
            assert "sea_surface_temp" in dataset.parameters
            assert "csv" in dataset.formats

    async def test_upload_propagates_spatial_extent_to_dataset(self, client, admin_headers):
        """dataset_files.spatial_extent is per-file; Phase 3's catalog detail
        endpoint reads datasets.spatial_extent, so ingestion must union each
        file's extent up onto the parent dataset, not leave it file-local."""
        import uuid as uuid_module

        from sqlalchemy import func, select

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import Dataset

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("spatial_test.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid_module.UUID(dataset_id))
            assert dataset.spatial_extent is not None

            row = (
                await db.execute(
                    select(
                        func.ST_XMin(dataset.spatial_extent),
                        func.ST_XMax(dataset.spatial_extent),
                        func.ST_YMin(dataset.spatial_extent),
                        func.ST_YMax(dataset.spatial_extent),
                    )
                )
            ).one()
            lon_min, lon_max, lat_min, lat_max = row
            # _make_csv_bytes(): lat = 22.0 + i*0.05, lon = 91.0 + i*0.05, i in [0, 14]
            assert lat_min == pytest.approx(22.0, abs=1e-3)
            assert lat_max == pytest.approx(22.7, abs=1e-3)
            assert lon_min == pytest.approx(91.0, abs=1e-3)
            assert lon_max == pytest.approx(91.7, abs=1e-3)

    async def test_second_upload_unions_spatial_extent_without_error(self, client, admin_headers):
        """Uploading a second file to a dataset that already has a
        spatial_extent (whether from a prior upload or, as with the seed
        script, a plain-WKT-string assignment outside the ORM's normal
        geometry write path) must not raise — ST_Union needs both sides
        explicitly cast to geometry, since the in-session Python value on
        dataset.spatial_extent isn't guaranteed to already be WKB-typed."""
        import uuid as uuid_module

        from sqlalchemy import func, select

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import Dataset

        dataset_id = await _create_dataset(client, admin_headers)

        # Simulate the seed script's plain-string assignment path (bypasses
        # the upload endpoint's normal geometry write) to reproduce the
        # exact condition that broke ST_Union before the cast fix.
        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid_module.UUID(dataset_id))
            dataset.spatial_extent = (
                "SRID=4326;POLYGON((90.0 20.0, 92.0 20.0, 92.0 23.0, 90.0 23.0, 90.0 20.0))"
            )
            await db.commit()

        files = {"file": ("second_upload.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        r2 = await client.get(f"/api/v1/admin/datasets/uploads/{r.json()['upload']['id']}", headers=admin_headers)
        assert r2.json()["status"] == "complete", r2.text

        async with AsyncSessionLocal() as db:
            dataset = await db.get(Dataset, uuid_module.UUID(dataset_id))
            row = (
                await db.execute(
                    select(
                        func.ST_XMin(dataset.spatial_extent),
                        func.ST_XMax(dataset.spatial_extent),
                    )
                )
            ).one()
            lon_min, lon_max = row
            # Union of the pre-existing [90,92] extent and the new file's
            # [91.0, 91.7] extent must cover both — envelope widens, not narrows.
            assert lon_min <= 90.0 + 1e-3
            assert lon_max >= 91.7 - 1e-3

    async def test_upload_to_nonexistent_dataset_404s(self, client, admin_headers):
        import uuid

        files = {"file": ("x.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{uuid.uuid4()}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 404

    async def test_upload_requires_permission(self, client, admin_headers):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("x.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(f"/api/v1/admin/datasets/{dataset_id}/files", files=files)
        assert r.status_code == 401


class TestMatUpload:
    async def test_upload_mat_succeeds(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("test.mat", _make_mat_bytes(tmp_path), "application/octet-stream")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text
        assert r.json()["dataset_file"]["file_format"] == "mat"


class TestGeoTiffUpload:
    async def test_upload_geotiff_succeeds_and_processes(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("test.tif", _make_geotiff_bytes(tmp_path), "image/tiff")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["dataset_file"]["file_format"] == "tif"
        upload_id = body["upload"]["id"]

        r = await client.get(f"/api/v1/admin/datasets/uploads/{upload_id}", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "complete"

    async def test_geotiff_populates_raster_metadata(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("raster_meta.tif", _make_geotiff_bytes(tmp_path), "image/tiff")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202
        body = r.json()["dataset_file"]
        assert body["file_metadata"]["shape"] == "raster"
        assert body["file_metadata"]["bands"] == ["sea_surface_temp"]
        assert body["file_metadata"]["pixel_width"] == 300
        assert body["file_metadata"]["pixel_height"] == 200

    async def test_rejects_geotiff_without_crs(self, client, admin_headers, tmp_path):
        p = tmp_path / "no_crs.tif"
        data = np.zeros((10, 10), dtype="uint8")
        with rasterio.open(p, "w", driver="GTiff", height=10, width=10, count=1, dtype="uint8") as dst:
            dst.write(data, 1)

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("no_crs.tif", p.read_bytes(), "image/tiff")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202
        upload_id = r.json()["upload"]["id"]

        r = await client.get(f"/api/v1/admin/datasets/uploads/{upload_id}", headers=admin_headers)
        assert r.json()["status"] == "failed"
        assert "coordinate reference system" in r.json()["error_message"]


class TestUploadValidation:
    async def test_rejects_unsupported_extension(self, client, admin_headers):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("test.exe", b"MZ\x90\x00fake executable content", "application/octet-stream")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 400
        assert "Unsupported file type" in r.json()["error"]["message"]

    async def test_rejects_content_extension_mismatch(self, client, admin_headers):
        dataset_id = await _create_dataset(client, admin_headers)
        # A plain text file lying about being a .mat
        files = {"file": ("fake.mat", b"this is definitely not a mat file", "application/octet-stream")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 400

    async def test_rejects_empty_file(self, client, admin_headers):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("empty.csv", b"", "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 400


class TestUploadListing:
    async def test_list_uploads_for_dataset(self, client, admin_headers):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("list_test.csv", _make_csv_bytes(), "text/csv")}
        await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )

        r = await client.get(f"/api/v1/admin/datasets/{dataset_id}/uploads", headers=admin_headers)
        assert r.status_code == 200
        uploads = r.json()
        assert len(uploads) == 1
        assert uploads[0]["file_name"] == "list_test.csv"

    async def test_get_status_for_unknown_upload_404s(self, client, admin_headers):
        import uuid

        r = await client.get(
            f"/api/v1/admin/datasets/uploads/{uuid.uuid4()}", headers=admin_headers
        )
        assert r.status_code == 404
