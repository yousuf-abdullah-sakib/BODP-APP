import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog import DatasetVariable, VariableDataType
from tests.test_dataset_upload import (
    _create_dataset,
    _make_csv_bytes,
    _make_geotiff_bytes,
    _make_mat_bytes,
    _make_netcdf_bytes,
)

pytestmark = pytest.mark.asyncio


async def _variables_for(dataset_id: str) -> list[DatasetVariable]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DatasetVariable).where(DatasetVariable.dataset_id == uuid.UUID(dataset_id))
        )
        return list(result.scalars().all())


class TestDatasetVariableRegistryCsv:
    async def test_csv_upload_registers_dimensions_and_data_variable(self, client, admin_headers):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("registry_test.csv", _make_csv_bytes(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        variables = await _variables_for(dataset_id)
        by_name = {v.name: v for v in variables}

        assert by_name["time"].is_dimension is True
        assert by_name["time"].data_type == VariableDataType.TEMPORAL.value
        assert by_name["lat"].is_dimension is True
        assert by_name["lon"].is_dimension is True

        sst = by_name["sea_surface_temp"]
        assert sst.is_dimension is False
        assert sst.data_type == VariableDataType.NUMERIC.value
        assert sst.roles == []  # unreviewed — Phase 3's job, not Phase 2's
        assert sst.min_value is not None and sst.max_value is not None
        assert float(sst.min_value) <= 27.0
        assert float(sst.max_value) >= 27.0

    async def test_categorical_column_registered_with_distinct_values(self, client, admin_headers):
        import pandas as pd
        import io

        df = pd.DataFrame(
            {
                "time": pd.date_range("2024-01-01", periods=6, freq="D"),
                "lat": [22.0 + i * 0.1 for i in range(6)],
                "lon": [91.0 + i * 0.1 for i in range(6)],
                "quality_grade": ["A", "B", "A", "C", "B", "A"],
            }
        )
        buf = io.BytesIO()
        df.to_csv(buf, index=False)

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("categorical_test.csv", buf.getvalue(), "text/csv")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        variables = await _variables_for(dataset_id)
        by_name = {v.name: v for v in variables}

        grade = by_name["quality_grade"]
        assert grade.data_type == VariableDataType.CATEGORICAL.value
        assert grade.is_dimension is False
        assert set(grade.distinct_values or []) == {"A", "B", "C"}
        assert grade.min_value is None and grade.max_value is None

    async def test_repeat_upload_widens_existing_variable_range(self, client, admin_headers):
        """A second file uploaded into the same dataset that redetects the
        same variable name should widen min/max, not create a duplicate
        DatasetVariable row."""
        import pandas as pd
        import io

        dataset_id = await _create_dataset(client, admin_headers)

        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files",
            files={"file": ("first.csv", _make_csv_bytes(), "text/csv")},
            headers=admin_headers,
        )
        assert r.status_code == 202

        df2 = pd.DataFrame(
            {
                "time": pd.date_range("2024-02-01", periods=5, freq="D"),
                "lat": [23.0 + i * 0.05 for i in range(5)],
                "lon": [92.0 + i * 0.05 for i in range(5)],
                "sea_surface_temp": [50.0 + i for i in range(5)],  # much higher than file 1
            }
        )
        buf2 = io.BytesIO()
        df2.to_csv(buf2, index=False)
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files",
            files={"file": ("second.csv", buf2.getvalue(), "text/csv")},
            headers=admin_headers,
        )
        assert r.status_code == 202

        variables = await _variables_for(dataset_id)
        sst_rows = [v for v in variables if v.name == "sea_surface_temp"]
        assert len(sst_rows) == 1  # widened, not duplicated
        assert float(sst_rows[0].max_value) >= 50.0


class TestDatasetVariableRegistryNetcdf:
    async def test_netcdf_upload_registers_variables_excluding_auxiliary_coords(
        self, client, admin_headers, tmp_path
    ):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("registry_test.nc", _make_netcdf_bytes(tmp_path), "application/x-netcdf")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        variables = await _variables_for(dataset_id)
        names = {v.name for v in variables}

        assert "u10" in names
        assert "valid_time" in names  # the detected time dimension
        # number/expver are auxiliary coordinates, never real data
        # variables — must not appear as registered variables either,
        # mirroring _write_dataset_records' own exclusion.
        assert "number" not in names
        assert "expver" not in names

    async def test_zarr_backed_upload_registers_variables_without_dataset_records(
        self, client, admin_headers, tmp_path, monkeypatch
    ):
        """End-to-end: a NetCDF file large enough to trigger the Zarr
        output path must still register its variables in the schema
        registry, but must NOT produce any DatasetRecord rows (a Zarr
        store has no tidy-table row concept — _write_dataset_records
        reads a Parquet file, which doesn't exist for this artifact)."""
        import uuid as uuid_module

        import numpy as np
        import pandas as pd
        import xarray as xr
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.catalog import DatasetRecord
        import app.services.parsers.netcdf_parser as netcdf_module

        monkeypatch.setattr(netcdf_module, "_ZARR_THRESHOLD_ELEMENTS", 10)

        n_time = 15
        times = pd.date_range("2024-01-01", periods=n_time, freq="D")
        lats = np.linspace(20.0, 21.0, 3)
        lons = np.linspace(90.0, 91.0, 3)
        rng = np.random.default_rng(4)
        sst = rng.random((n_time, 3, 3)) * 30
        ds = xr.Dataset(
            {"sea_surface_temp": (("time", "lat", "lon"), sst)},
            coords={"time": times, "lat": lats, "lon": lons},
        )
        p = tmp_path / "zarr_trigger.nc"
        ds.to_netcdf(p)

        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("zarr_trigger.nc", p.read_bytes(), "application/x-netcdf")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text
        assert r.json()["dataset_file"]["file_metadata"]["processed_key"].endswith(".zarr.zip")

        variables = await _variables_for(dataset_id)
        names = {v.name for v in variables}
        assert "sea_surface_temp" in names
        assert "time" in names

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatasetRecord).where(DatasetRecord.dataset_id == uuid_module.UUID(dataset_id))
            )
            assert result.scalars().all() == []


class TestDatasetVariableRegistryMat:
    async def test_mat_upload_registers_variables(self, client, admin_headers, tmp_path):
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("registry_test.mat", _make_mat_bytes(tmp_path), "application/octet-stream")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        variables = await _variables_for(dataset_id)
        names = {v.name for v in variables}
        assert "temperature" in names
        assert "lat" in names
        assert "lon" in names


class TestDatasetVariableRegistryGeoTiff:
    async def test_geotiff_upload_registers_bands_without_range(self, client, admin_headers, tmp_path):
        """Raster bands are registered as variables but without a computed
        min/max range — computing that would require reading pixel data,
        explicitly out of scope for the lightweight registry write."""
        dataset_id = await _create_dataset(client, admin_headers)
        files = {"file": ("registry_test.tif", _make_geotiff_bytes(tmp_path), "image/tiff")}
        r = await client.post(
            f"/api/v1/admin/datasets/{dataset_id}/files", files=files, headers=admin_headers
        )
        assert r.status_code == 202, r.text

        variables = await _variables_for(dataset_id)
        assert len(variables) == 1
        band = variables[0]
        assert band.name == "sea_surface_temp"
        assert band.is_dimension is False
        assert band.data_type == VariableDataType.NUMERIC.value
        assert band.min_value is None and band.max_value is None
