from datetime import date, timedelta

import pytest

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetStatus
from app.models.stats import DailyStatsSnapshot
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client) -> dict:
    token = await register_verified_user(client, email="overview-admin@example.com", admin=True)
    return {"Authorization": f"Bearer {token}"}


class TestOverviewStats:
    async def test_reflects_real_counts(self, client):
        headers = await _admin_headers(client)

        async with AsyncSessionLocal() as db:
            db.add(Dataset(code="BD-OV-1", title="Overview Dataset 1", status=DatasetStatus.DRAFT.value))
            db.add(
                Dataset(code="BD-OV-2", title="Overview Dataset 2", status=DatasetStatus.PUBLISHED.value)
            )
            await db.commit()

        r = await client.get("/api/v1/admin/overview", headers=headers)
        assert r.status_code == 200
        body = r.json()
        # The one admin fixture user itself counts too.
        assert body["total_users"]["current"] == 1
        assert body["total_datasets"]["current"] == 2

    async def test_requires_admin(self, client):
        token = await register_verified_user(client, email="overview-nonadmin@example.com")
        r = await client.get(
            "/api/v1/admin/overview", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403

    async def test_any_admin_passes_regardless_of_fine_grained_permissions(self, client):
        # require_admin is a coarse gate — an admin with zero fine-grained
        # roles/permissions must still be able to view the overview.
        token = await register_verified_user(
            client, email="overview-bare-admin@example.com", admin=True, permissions=None
        )
        r = await client.get(
            "/api/v1/admin/overview", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200


class TestTrendDeltas:
    async def test_delta_none_with_fewer_than_two_snapshots(self, client):
        headers = await _admin_headers(client)
        r = await client.get("/api/v1/admin/overview", headers=headers)
        assert r.json()["total_datasets"]["delta_pct"] is None

    async def test_delta_computed_from_real_snapshots(self, client):
        headers = await _admin_headers(client)

        async with AsyncSessionLocal() as db:
            db.add(
                DailyStatsSnapshot(
                    snapshot_date=date.today() - timedelta(days=1),
                    total_users=1,
                    total_datasets=2,
                    pending_requests=0,
                    total_downloads=0,
                    active_grants=0,
                    storage_used_bytes=0,
                )
            )
            db.add(
                DailyStatsSnapshot(
                    snapshot_date=date.today() - timedelta(days=2),
                    total_users=1,
                    total_datasets=4,
                    pending_requests=0,
                    total_downloads=0,
                    active_grants=0,
                    storage_used_bytes=0,
                )
            )
            db.add(Dataset(code="BD-TREND-1", title="Trend Dataset", status=DatasetStatus.DRAFT.value))
            await db.commit()

        r = await client.get("/api/v1/admin/overview", headers=headers)
        body = r.json()
        assert body["total_datasets"]["current"] == 1
        # previous = the most recent snapshot (yesterday's value=2), not
        # the older one (2 days ago, value=4) — trend compares against the
        # latest captured day, and the sparkline stays oldest -> newest.
        assert body["total_datasets"]["previous"] == 2
        assert body["total_datasets"]["delta_pct"] == -50.0
        assert body["total_datasets"]["sparkline"] == [4, 2]


class TestSystemHealth:
    async def test_healthy_by_default(self, client):
        headers = await _admin_headers(client)
        r = await client.get("/api/v1/admin/overview", headers=headers)
        health = r.json()["system_health"]
        assert health["database"] is True
        assert health["redis"] is True
        assert health["storage"] is True

    async def test_redis_failure_isolated_to_redis_flag(self, client, monkeypatch):
        headers = await _admin_headers(client)

        from app.services import admin_overview_service

        class _FailingRedis:
            async def ping(self):
                raise ConnectionError("redis down")

        monkeypatch.setattr(admin_overview_service, "get_redis", lambda: _FailingRedis())

        r = await client.get("/api/v1/admin/overview", headers=headers)
        health = r.json()["system_health"]
        assert health["redis"] is False
        assert health["database"] is True
        assert health["storage"] is True


class TestStorageCapacity:
    async def test_null_capacity_has_no_percentage(self, client):
        headers = await _admin_headers(client)
        r = await client.get("/api/v1/admin/overview", headers=headers)
        storage = r.json()["storage"]
        assert storage["capacity_bytes"] is None
        assert storage["percent_used"] is None

    async def test_configured_capacity_computes_percentage(self, client):
        headers = await _admin_headers(client)
        r = await client.patch(
            "/api/v1/admin/settings/storage-capacity",
            json={"storage_capacity_bytes": 1000},
            headers=headers,
        )
        assert r.status_code == 200

        r = await client.get("/api/v1/admin/overview", headers=headers)
        storage = r.json()["storage"]
        assert storage["capacity_bytes"] == 1000
        assert storage["percent_used"] == 0.0
