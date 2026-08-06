from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_email_verification_token
from app.models.catalog import Dataset, DatasetCategory
from app.models.requests import AccessGrant, DatasetRequest, DownloadLog, RequestStatus
from app.models.user import User
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"analytics-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _create_verified_user(client, *, email: str) -> str:
    """Registers + verifies a plain (non-admin) user, returning its id."""
    r = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Analytics Fixture", "email": email, "password": "testpass123", "institution": None, "phone": None},
    )
    assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        token = create_email_verification_token(str(user.id), user.email)
        user_id = str(user.id)
    r = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200
    return user_id


async def _seed_analytics_fixture(client) -> dict:
    """Seeds a small, fully-known dataset of requests/downloads/users so the
    aggregation counts can be asserted exactly, not just checked for 200."""
    now = datetime.now(UTC)
    in_range_1 = now - timedelta(days=10)
    in_range_2 = now - timedelta(days=5)
    out_of_range = now - timedelta(days=400)  # outside the default 365-day window

    requester_id = await _create_verified_user(client, email="analytics-requester@example.com")

    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name="Analytics Category", color_tag="cat-analytics")
        db.add(category)
        await db.flush()

        dataset = Dataset(code="BD-ANALYTICS1", title="Analytics Dataset", category_id=category.id)
        db.add(dataset)
        await db.flush()

        # Two approved requests + one pending, all in range -> approval rate 2/3.
        req1 = DatasetRequest(
            user_id=requester_id, dataset_id=dataset.id, justification="j1",
            status=RequestStatus.APPROVED.value, submitted_at=in_range_1,
        )
        req2 = DatasetRequest(
            user_id=requester_id, dataset_id=dataset.id, justification="j2",
            status=RequestStatus.APPROVED.value, submitted_at=in_range_2,
        )
        req3 = DatasetRequest(
            user_id=requester_id, dataset_id=dataset.id, justification="j3",
            status=RequestStatus.PENDING.value, submitted_at=in_range_2,
        )
        # One request outside the default 365-day window — must not be counted.
        req_out = DatasetRequest(
            user_id=requester_id, dataset_id=dataset.id, justification="j4",
            status=RequestStatus.APPROVED.value, submitted_at=out_of_range,
        )
        db.add_all([req1, req2, req3, req_out])
        await db.flush()

        grant = AccessGrant(
            user_id=requester_id, dataset_id=dataset.id, request_id=req1.id,
            granted_at=in_range_1, expires_at=now + timedelta(days=30),
        )
        db.add(grant)
        await db.flush()

        # Three downloads in range, one out of range.
        dl1 = DownloadLog(grant_id=grant.id, user_id=requester_id, downloaded_at=in_range_1)
        dl2 = DownloadLog(grant_id=grant.id, user_id=requester_id, downloaded_at=in_range_2)
        dl3 = DownloadLog(grant_id=grant.id, user_id=requester_id, downloaded_at=in_range_2)
        dl_out = DownloadLog(grant_id=grant.id, user_id=requester_id, downloaded_at=out_of_range)
        db.add_all([dl1, dl2, dl3, dl_out])

        await db.commit()

    return {"category": category.name, "dataset_title": dataset.title}


class TestAnalyticsAggregation:
    async def test_exact_counts_over_default_window(self, client):
        headers = await _admin_headers(client, permissions=["View Analytics"])
        fixture = await _seed_analytics_fixture(client)

        r = await client.get("/api/v1/admin/analytics", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["requests_submitted"] == 3
        assert body["approval_rate_pct"] == pytest.approx(66.7, abs=0.1)
        assert body["total_downloads"] == 3
        assert body["requests_by_status"]["approved"] == 2
        assert body["requests_by_status"]["pending"] == 1

        categories = {c["category"]: c["count"] for c in body["requests_by_category"]}
        assert categories[fixture["category"]] == 3

        top = {t["title"]: t["count"] for t in body["top_datasets_by_grants"]}
        assert top[fixture["dataset_title"]] == 1

    async def test_new_researchers_counted_within_range(self, client):
        headers = await _admin_headers(client, permissions=["View Analytics"])
        await _create_verified_user(client, email="fresh-researcher@example.com")

        r = await client.get("/api/v1/admin/analytics", headers=headers)
        assert r.status_code == 200
        # At least the freshly-created researcher + the admin fixture user itself.
        assert r.json()["new_researchers"] >= 1

    async def test_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.get("/api/v1/admin/analytics", headers=headers)
        assert r.status_code == 403
