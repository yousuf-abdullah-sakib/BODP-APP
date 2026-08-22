"""PLAN.md Phase 5 (Storage & Query Architecture) — proves subset
extraction/download works correctly against a CHUNKED_ARRAY (Zarr-backed)
dataset, closing the real gap confirmed during planning: extraction.py's
_source_for_format() has no single processed_key object to resolve for a
Zarr store (only a processed_prefix multi-object MinIO layout), so csv/
parquet output requests against gridded data would otherwise fail. This
is explicitly required, non-deferrable work per PLAN.md's Definition of
Done ("build a Zarr-aware extraction path... not a discovered gap to
defer"). Mirrors test_extraction.py's existing fixture pattern, but
uploads a genuinely gridded NetCDF (routes to Zarr, not Parquet).
"""

import io
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetCategory, DatasetFile, DatasetStatus, StorageBackend
from app.models.requests import AccessGrant, GrantStatus
from app.models.user import User
from app.services.storage.registry import get_storage_backend
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


def _make_gridded_netcdf_bytes(tmp_path) -> bytes:
    """3 timesteps x 3x3 grid, distinct per-cell values (via a linear
    formula) so a filtered subset's exact expected values are hand-
    computable, not just non-empty."""
    p = tmp_path / "extract_phase5_grid.nc"
    times = pd.date_range("2024-03-01", periods=3, freq="D")
    lats = np.array([20.0, 20.5, 21.0])
    lons = np.array([90.0, 90.5, 91.0])
    data = np.zeros((3, 3, 3))
    for t in range(3):
        for i in range(3):
            for j in range(3):
                data[t, i, j] = 100 * t + 10 * i + j
    ds = xr.Dataset(
        {"sea_surface_temp": (("time", "lat", "lon"), data)},
        coords={"time": times, "lat": lats, "lon": lons},
    )
    ds.to_netcdf(p)
    return p.read_bytes()


def _make_two_variable_gridded_netcdf_bytes(tmp_path) -> bytes:
    """Same 3x3x3 grid shape as _make_gridded_netcdf_bytes, but with TWO
    distinct data variables — needed to prove a parameter-scoped NetCDF
    extraction includes only the requested variable and genuinely excludes
    the other, not just "produces non-empty output" (Data Page Filter &
    Extraction Audit, critical #2)."""
    p = tmp_path / "extract_phase5_grid_2var.nc"
    times = pd.date_range("2024-03-01", periods=3, freq="D")
    lats = np.array([20.0, 20.5, 21.0])
    lons = np.array([90.0, 90.5, 91.0])
    sst = np.zeros((3, 3, 3))
    salinity = np.zeros((3, 3, 3))
    for t in range(3):
        for i in range(3):
            for j in range(3):
                sst[t, i, j] = 100 * t + 10 * i + j
                salinity[t, i, j] = 35.0 + 0.1 * (100 * t + 10 * i + j)
    ds = xr.Dataset(
        {
            "sea_surface_temp": (("time", "lat", "lon"), sst),
            "salinity": (("time", "lat", "lon"), salinity),
        },
        coords={"time": times, "lat": lats, "lon": lons},
    )
    ds.to_netcdf(p)
    return p.read_bytes()


async def _admin_headers(client) -> dict:
    token = await register_verified_user(
        client, email="extract5-admin@example.com", admin=True,
        permissions=["Approve Requests", "Edit Datasets"],
    )
    return {"Authorization": f"Bearer {token}"}


async def _researcher(client, email: str) -> dict:
    token = await register_verified_user(client, email=email)
    return {"Authorization": f"Bearer {token}"}


async def _gridded_dataset(client, admin_headers, tmp_path, *, code: str, file_bytes: bytes | None = None) -> str:
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name=f"Cat-{code}", description="test", color_tag="cat-Environmental")
        db.add(category)
        await db.flush()
        dataset = Dataset(
            code=code,
            title=f"Zarr Extraction Test {code}",
            description="test",
            category_id=category.id,
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.commit()
        await db.refresh(dataset)
        dataset_id = str(dataset.id)

    files = {"file": (f"{code}.nc", file_bytes or _make_gridded_netcdf_bytes(tmp_path), "application/x-netcdf")}
    r = await client.post(
        f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
    )
    assert r.status_code == 202, r.text
    file_metadata = r.json()["dataset_file"]["file_metadata"]
    assert file_metadata["shape"] == "gridded"
    assert file_metadata["storage_kind"] == "chunked_array"
    return dataset_id


async def _approved_grant(client, admin_headers, researcher_email: str, *, dataset_id: str) -> str:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == researcher_email))
        user = result.scalar_one()
        grant = AccessGrant(
            user_id=user.id,
            dataset_id=uuid.UUID(dataset_id),
            granted_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=30),
            status=GrantStatus.ACTIVE.value,
            scope=None,
        )
        db.add(grant)
        await db.commit()
        await db.refresh(grant)
        return str(grant.id)


async def _fetch_extraction_output_bytes(extraction_id: str) -> bytes:
    """Reads the extraction's stored output directly via the storage
    backend (bypassing the presigned-URL download endpoint, which isn't
    reachable from inside the test runner against the internal MinIO
    hostname) — same data, just fetched the way the storage layer itself
    would serve it."""
    from app.models.requests import SubsetExtraction

    async with AsyncSessionLocal() as db:
        extraction = await db.get(SubsetExtraction, uuid.UUID(extraction_id))
        storage = get_storage_backend(extraction.output_storage_backend or StorageBackend.VPS_MINIO.value)
        body = storage.get(extraction.output_bucket, extraction.output_file_key)
        return body.read()


class TestZarrBackedExtraction:
    async def test_csv_extraction_from_gridded_dataset_produces_correct_values(
        self, client, tmp_path
    ):
        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-csv@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _gridded_dataset(client, admin_headers, tmp_path, code="BD-EXT5-CSV")
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text

        content = await _fetch_extraction_output_bytes(extraction_id)
        df = pd.read_csv(io.BytesIO(content))
        assert len(df) == 27  # 3 time x 3 lat x 3 lon
        assert "sea_surface_temp" in df.columns
        # Cell (t=0, lat=20.0, lon=90.0) -> value 0 by the fixture's formula.
        row = df[(df["time"].str.startswith("2024-03-01")) & (df["lat"] == 20.0) & (df["lon"] == 90.0)]
        assert row["sea_surface_temp"].iloc[0] == pytest.approx(0.0)
        # Cell (t=2, lat=21.0, lon=91.0) -> value 100*2+10*2+2 = 222.
        row2 = df[(df["time"].str.startswith("2024-03-03")) & (df["lat"] == 21.0) & (df["lon"] == 91.0)]
        assert row2["sea_surface_temp"].iloc[0] == pytest.approx(222.0)

    async def test_csv_extraction_narrows_to_requested_date_range(self, client, tmp_path):
        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-scope@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _gridded_dataset(client, admin_headers, tmp_path, code="BD-EXT5-SCOPE")
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {"date_from": "2024-03-02", "date_to": "2024-03-02"}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text

        content = await _fetch_extraction_output_bytes(extraction_id)
        df = pd.read_csv(io.BytesIO(content))
        assert len(df) == 9  # only 2024-03-02's 3x3 grid
        assert set(df["time"].str[:10]) == {"2024-03-02"}

    async def test_parquet_extraction_from_gridded_dataset(self, client, tmp_path):
        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-parquet@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _gridded_dataset(client, admin_headers, tmp_path, code="BD-EXT5-PARQUET")
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "parquet"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text

        content = await _fetch_extraction_output_bytes(extraction_id)
        df = pd.read_parquet(io.BytesIO(content))
        assert len(df) == 27
        assert "sea_surface_temp" in df.columns

    async def test_netcdf_extraction_from_gridded_dataset_still_works(self, client, tmp_path):
        """The raw-original-file path (NetcdfExtractor reading
        dataset_file.storage_key directly) was never affected by Phase 5's
        Zarr-multi-object change — regression guard confirming netcdf
        output still works unmodified for a gridded/Zarr-backed file."""
        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-netcdf@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _gridded_dataset(client, admin_headers, tmp_path, code="BD-EXT5-NETCDF")
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "netcdf"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text
        assert r2.json()["output_size_bytes"] > 0


class TestChecksumOnRead:
    """Phase 10.5 — netcdf-format extraction is the one output format
    whose source read resolves to the RAW upload (_source_for_format's
    csv/parquet branch reads the processed artifact instead, which has
    no checksum recorded against it) — the only extraction path this
    check can actually exercise end-to-end."""

    async def test_extraction_fails_cleanly_when_stored_bytes_are_corrupted(self, client, tmp_path):
        import uuid as uuid_module

        from app.models.catalog import DatasetFile
        from app.models.requests import ExtractionStatus, SubsetExtraction
        from app.services.storage.registry import get_storage_backend
        from app.worker.tasks.extraction import process_extraction

        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-checksum@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _gridded_dataset(client, admin_headers, tmp_path, code="BD-EXT5-CHECKSUM")
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        async with AsyncSessionLocal() as db:
            dataset_file = (
                await db.execute(select(DatasetFile).where(DatasetFile.dataset_id == uuid.UUID(dataset_id)))
            ).scalar_one()
            storage_bucket = dataset_file.storage_bucket
            storage_key = dataset_file.storage_key
            assert dataset_file.checksum is not None

        storage = get_storage_backend("vps_minio")
        storage.put(storage_bucket, storage_key, io.BytesIO(b"CORRUPTED, NOT A REAL NETCDF FILE"), content_type="application/x-netcdf")

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one()
            grant = (
                await db.execute(select(AccessGrant).where(AccessGrant.dataset_id == uuid.UUID(dataset_id)))
            ).scalar_one()
            extraction = SubsetExtraction(
                grant_id=grant.id,
                requested_scope={},
                format="netcdf",
                status=ExtractionStatus.QUEUED.value,
            )
            db.add(extraction)
            await db.commit()
            await db.refresh(extraction)
            extraction_id = str(extraction.id)

        # process_extraction uses a sync session internally (get_sync_db)
        # — run it directly, same as the Celery task would, without
        # needing a broker round trip.
        from app.core.database import get_sync_db

        with get_sync_db() as sync_db:
            result_dict = process_extraction(sync_db, extraction_id)

        assert result_dict["status"] == "failed"
        assert "checksum verification" in result_dict["reason"]

        r = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "failed"
        assert "checksum verification" in body["error_message"]


class TestNetcdfParameterFiltering:
    """Data Page Filter & Extraction Audit, critical #2: NetcdfExtractor
    read scope.get("parameter") (singular) instead of scope["parameters"]
    (plural, the field SearchCriteriaSchema/requested_scope actually use)
    — the singular key never exists in any real stored scope, so this was
    a permanent no-op that silently included every variable regardless of
    the user's selection. Uses a genuinely two-variable fixture so
    "parameter filtering worked" means "the OTHER variable is actually
    gone," not just "the file isn't empty.\""""

    async def test_netcdf_extraction_includes_only_requested_parameter(self, client, tmp_path):
        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-netcdf-param@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _gridded_dataset(
            client, admin_headers, tmp_path, code="BD-EXT5-NCPARAM",
            file_bytes=_make_two_variable_gridded_netcdf_bytes(tmp_path),
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {"parameters": ["sea_surface_temp"]}, "format": "netcdf"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text

        content = await _fetch_extraction_output_bytes(extraction_id)
        with xr.open_dataset(io.BytesIO(content)) as ds:
            assert "sea_surface_temp" in ds.data_vars
            assert "salinity" not in ds.data_vars, (
                "requested only sea_surface_temp but salinity is still present — "
                "parameter filtering silently did nothing"
            )

    async def test_netcdf_extraction_with_no_parameter_scope_includes_all_variables(self, client, tmp_path):
        """Control case: an empty/omitted parameters scope must still
        include every variable (matches apply_scope_mask's "empty list ==
        no filtering" convention) — the fix must not accidentally start
        excluding everything when nothing was requested."""
        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-netcdf-noparam@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _gridded_dataset(
            client, admin_headers, tmp_path, code="BD-EXT5-NCNOPARAM",
            file_bytes=_make_two_variable_gridded_netcdf_bytes(tmp_path),
        )
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "netcdf"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text

        content = await _fetch_extraction_output_bytes(extraction_id)
        with xr.open_dataset(io.BytesIO(content)) as ds:
            assert "sea_surface_temp" in ds.data_vars
            assert "salinity" in ds.data_vars


class TestZarrExtractionSpatialBounds:
    """Data Page Filter & Extraction Audit, critical #1's downstream half:
    the request-payload fix (frontend) ensures typed lat/lon reaches
    stored search_criteria/requested_scope as a `bounds` dict — this
    verifies that once `bounds` IS present in the stored scope (exactly
    the shape the fixed frontend now produces for typed-coordinate
    requests, indistinguishable on the backend from a drawn AOI), a Zarr-
    backed extraction genuinely narrows to it, for both a spatial subset
    that should keep some cells and one that should exclude the rest."""

    async def test_extraction_narrows_to_requested_bounds(self, client, tmp_path):
        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-bounds@example.com"
        researcher_headers = await _researcher(client, email)
        dataset_id = await _gridded_dataset(client, admin_headers, tmp_path, code="BD-EXT5-BOUNDS")
        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        # Fixture grid is lat in {20.0, 20.5, 21.0}, lon in {90.0, 90.5, 91.0}
        # (see _make_gridded_netcdf_bytes) — narrow to exactly the single
        # (lat=20.0, lon=90.0) cell across all 3 timesteps.
        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={
                "scope": {"bounds": {"lat_min": 19.9, "lat_max": 20.1, "lon_min": 89.9, "lon_max": 90.1}},
                "format": "csv",
            },
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text

        content = await _fetch_extraction_output_bytes(extraction_id)
        df = pd.read_csv(io.BytesIO(content))
        assert len(df) == 3  # 1 cell x 3 timesteps
        assert set(df["lat"].round(1)) == {20.0}
        assert set(df["lon"].round(1)) == {90.0}


class TestMixedStorageKindExtraction:
    """A Dataset with one ROW_RECORDS-equivalent... actually a Parquet
    tabular file AND a Zarr gridded file — both files' extracted subsets
    must combine into one zip bundle (bundle_as_zip's existing behavior
    for >1 file, exercised here for the first time across mixed storage
    kinds)."""

    async def test_extraction_combines_parquet_and_zarr_files_into_one_bundle(
        self, client, tmp_path
    ):
        admin_headers = await _admin_headers(client)
        email = "extract5-researcher-mixed@example.com"
        researcher_headers = await _researcher(client, email)

        async with AsyncSessionLocal() as db:
            category = DatasetCategory(name="Cat-EXT5-MIXED", description="test", color_tag="cat-Environmental")
            db.add(category)
            await db.flush()
            dataset = Dataset(
                code="BD-EXT5-MIXED",
                title="Mixed Storage Extraction Test",
                description="test",
                category_id=category.id,
                status=DatasetStatus.PUBLISHED.value,
                record_count=0,
            )
            db.add(dataset)
            await db.commit()
            await db.refresh(dataset)
            dataset_id = str(dataset.id)

        csv_df = pd.DataFrame(
            {
                "time": pd.date_range("2024-01-01", periods=5, freq="D"),
                "lat": [22.0] * 5,
                "lon": [91.0] * 5,
                "salinity": [35.0, 35.1, 35.2, 35.1, 35.0],
            }
        )
        buf = io.BytesIO()
        csv_df.to_csv(buf, index=False)
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files",
            files={"file": ("mixed_tabular.csv", buf.getvalue(), "text/csv")},
            headers=admin_headers,
        )
        assert r.status_code == 202, r.text
        assert r.json()["dataset_file"]["file_metadata"]["storage_kind"] == "parquet"

        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files",
            files={
                "file": (
                    "mixed_grid.nc",
                    _make_gridded_netcdf_bytes(tmp_path),
                    "application/x-netcdf",
                )
            },
            headers=admin_headers,
        )
        assert r.status_code == 202, r.text
        assert r.json()["dataset_file"]["file_metadata"]["storage_kind"] == "chunked_array"

        async with AsyncSessionLocal() as db:
            count = (
                await db.execute(select(DatasetFile).where(DatasetFile.dataset_id == uuid.UUID(dataset_id)))
            ).scalars().all()
            assert len(count) == 2

        grant_id = await _approved_grant(client, admin_headers, email, dataset_id=dataset_id)

        r = await client.post(
            f"/api/v1/me/grants/{grant_id}/extract",
            json={"scope": {}, "format": "csv"},
            headers=researcher_headers,
        )
        assert r.status_code in (200, 202), r.text
        extraction_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/me/extractions/{extraction_id}", headers=researcher_headers)
        assert r2.json()["status"] == "complete", r2.text

        content = await _fetch_extraction_output_bytes(extraction_id)
        # Two files -> bundle_as_zip produces a real zip, not a bare CSV.
        import zipfile

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
            assert len(names) == 2
