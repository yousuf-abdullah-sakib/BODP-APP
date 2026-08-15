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


def _make_netcdf_bytes(tmp_path) -> bytes:
    """ERA5-style NetCDF: real data variables (u10) alongside non-dimensional
    auxiliary coordinates (number, expver) that xarray's to_dataframe()
    flattens into ordinary-looking columns but that are NOT scientific
    variables — used to prove _write_dataset_records only turns genuine
    data variables into DatasetRecord rows, not every leftover column."""
    import xarray as xr

    p = tmp_path / "upload_test.nc"
    times = pd.date_range("2024-01-01", periods=3)
    lats = np.array([20.5, 21.0])
    lons = np.array([90.0, 90.5])
    rng = np.random.default_rng(11)
    wind = rng.random((3, 2, 2)) * 10
    ds = xr.Dataset(
        {"u10": (("valid_time", "lat", "lon"), wind)},
        coords={
            "valid_time": times,
            "lat": lats,
            "lon": lons,
            "number": 0,
            "expver": "0001",
        },
    )
    ds.to_netcdf(p)
    return p.read_bytes()


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

    async def test_upload_populates_dataset_records(self, client, admin_headers):
        """Regression test: the real ingestion pipeline used to update only
        dataset.record_count (a summary counter) and never actually insert
        DatasetRecord rows — meaning catalog filtering silently returned
        nothing for every real upload despite the dataset card showing a
        nonzero record count. Verifies the rows genuinely exist now, are
        attributed to the right dataset/parameter, and satisfy the catalog
        filter query the same way seeded demo data always has."""
        import uuid as uuid_module

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import DatasetRecord

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("records_test.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetRecord).where(DatasetRecord.dataset_id == uuid_module.UUID(dataset_id))
            )
            records = result.scalars().all()

        # 15 source rows x 1 real variable column (sea_surface_temp) — lat/
        # lon/time are coordinate columns, not turned into their own rows.
        assert len(records) == 15
        assert all(rec.parameter == "sea_surface_temp" for rec in records)
        assert all(rec.value is not None for rec in records)
        assert all(rec.lat is not None and rec.lon is not None and rec.time is not None for rec in records)

        # Exercise the actual query the catalog's filter/records endpoint
        # uses (catalog_service.get_filtered_records) directly, rather than
        # the public HTTP router — the router additionally requires
        # status='published', which is a separate, unrelated gate this
        # admin-created test dataset (status='draft') doesn't need to pass
        # to prove the DatasetRecord population bug itself is fixed.
        from app.services.catalog_service import RecordsFilter, get_filtered_records

        async with AsyncSessionLocal() as db:
            _, matching_count, dataset_total_count, _ = await get_filtered_records(
                db, uuid_module.UUID(dataset_id), RecordsFilter(), preview_limit=6
            )
        assert matching_count == 15
        assert dataset_total_count == 15

    async def test_upload_timeless_csv_still_populates_records(self, client, admin_headers):
        """Phase 2 regression test: a static spatial grid CSV (lat/lon
        present, no time column at all — exactly Wave Data's real shape,
        discovered in Sub-phase A) must now populate real DatasetRecord
        rows with time=None, instead of the pre-Phase-2 behavior of
        writing zero rows because time/lat/lon were all required."""
        import uuid as uuid_module

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import DatasetRecord

        df = pd.DataFrame(
            {
                "lat": [22.0 + i * 0.05 for i in range(10)],
                "lon": [91.0 + i * 0.05 for i in range(10)],
                "wave_height": [1.5 + i * 0.1 for i in range(10)],
            }
        )
        buf = io.BytesIO()
        df.to_csv(buf, index=False)

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("timeless_grid.csv", buf.getvalue(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetRecord).where(DatasetRecord.dataset_id == uuid_module.UUID(dataset_id))
            )
            records = result.scalars().all()

        assert len(records) == 10
        assert all(rec.parameter == "wave_height" for rec in records)
        assert all(rec.time is None for rec in records)
        assert all(rec.lat is not None and rec.lon is not None for rec in records)

    async def test_upload_non_spatial_time_series_still_populates_records(self, client, admin_headers):
        """Mirror case: a time series with no lat/lon at all (e.g. a single
        buoy's own time-indexed readings with location implicit) must also
        populate real rows with lat/lon=None, not be skipped."""
        import uuid as uuid_module

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import DatasetRecord

        df = pd.DataFrame(
            {
                "time": pd.date_range("2024-01-01", periods=5, freq="D"),
                "air_pressure": [1010.0 + i for i in range(5)],
            }
        )
        buf = io.BytesIO()
        df.to_csv(buf, index=False)

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("time_only.csv", buf.getvalue(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetRecord).where(DatasetRecord.dataset_id == uuid_module.UUID(dataset_id))
            )
            records = result.scalars().all()

        assert len(records) == 5
        assert all(rec.parameter == "air_pressure" for rec in records)
        assert all(rec.time is not None for rec in records)
        assert all(rec.lat is None and rec.lon is None for rec in records)
        assert all(rec.geom is None for rec in records)

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


class TestNetcdfUpload:
    async def test_upload_netcdf_records_exclude_auxiliary_coordinates(
        self, client, admin_headers, tmp_path
    ):
        """Regression test: an earlier version of _write_dataset_records
        treated every non-lat/lon/time Parquet column as a real variable,
        which meant xarray's to_dataframe() flattening non-dimensional
        auxiliary coordinates (e.g. ERA5's "number"/"expver") in produced
        DatasetRecord rows for those too — polluting real data with
        meaningless bookkeeping values. Only genuine data variables
        (metadata.variables, i.e. ds.data_vars) may become parameter rows."""
        import uuid as uuid_module

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import DatasetRecord

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("aux_coords.nc", _make_netcdf_bytes(tmp_path), "application/x-netcdf")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text
        assert r.json()["dataset_file"]["file_metadata"]["variables"] == ["u10"]

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetRecord).where(DatasetRecord.dataset_id == uuid_module.UUID(dataset_id))
            )
            records = result.scalars().all()

        # 3 time steps x 2 lat x 2 lon = 12 rows for the one real variable.
        assert len(records) == 12
        assert {rec.parameter for rec in records} == {"u10"}


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

    async def test_geotiff_upload_never_creates_dataset_records(self, client, admin_headers, tmp_path):
        """Explicit negative test: raster files must NOT create one
        DatasetRecord per pixel (the test fixture is 300x200 = 60,000
        pixels — if the raster-skip check in _write_dataset_records ever
        regressed, this would immediately produce 60,000 rows for a single
        small test file). Raster data is meant to be queried through its
        COG in processed/, never as per-pixel rows."""
        import uuid as uuid_module

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import DatasetRecord

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("no_records.tif", _make_geotiff_bytes(tmp_path), "image/tiff")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetRecord).where(DatasetRecord.dataset_id == uuid_module.UUID(dataset_id))
            )
            assert result.scalars().all() == []

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

    async def test_admin_configured_size_limit_is_enforced(self, client, admin_headers):
        """Regression test: the upload endpoint used to always fall back to
        the hardcoded MAX_UPLOAD_SIZE_MB env default, silently ignoring
        site_settings.max_upload_size_mb even though it's admin-editable via
        Settings. Proves the DB-configured value is now actually read and
        enforced, not just displayed in the settings form."""
        r = await client.patch(
            "/api/v1/admin/settings/general",
            json={"max_upload_size_mb": 1},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["max_upload_size_mb"] == 1

        try:
            dataset_id = await _create_dataset(client, admin_headers)
            # 2MB of payload — comfortably over the 1MB limit just configured,
            # comfortably under the original 5000MB hardcoded default (which
            # would have wrongly accepted this file before the fix).
            oversized_csv = b"time,lat,lon,value\n" + (b"2024-01-01,22.0,91.0,27.0\n" * 100_000)
            assert len(oversized_csv) > 1 * 1024 * 1024

            files = {"file": ("oversized.csv", oversized_csv, "text/csv")}
            r = await client.post(
                f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
            )
            assert r.status_code == 400, r.text
            assert "exceeds the 1MB upload limit" in r.json()["error"]["message"]
        finally:
            # Restore the default so this test can't leak state into others.
            r = await client.patch(
                "/api/v1/admin/settings/general",
                json={"max_upload_size_mb": 5000},
                headers=admin_headers,
            )
            assert r.status_code == 200, r.text


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
