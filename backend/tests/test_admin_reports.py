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
        client, email=f"reports-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_report_fixture(client) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Report Fixture", "email": "report-fixture@example.com", "password": "testpass123", "institution": None, "phone": None},
    )
    assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == "report-fixture@example.com"))
        user = result.scalar_one()
        token = create_email_verification_token(str(user.id), user.email)
        user_id = user.id
    r = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200

    now = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name="Report Category", color_tag="cat-report")
        db.add(category)
        await db.flush()

        dataset = Dataset(code="BD-REPORT1", title="Report Dataset", category_id=category.id, status="published")
        db.add(dataset)
        await db.flush()

        req = DatasetRequest(
            user_id=user_id, dataset_id=dataset.id, justification="need it",
            status=RequestStatus.APPROVED.value, submitted_at=now - timedelta(days=2),
            reviewed_at=now - timedelta(days=1),
        )
        db.add(req)
        await db.flush()

        grant = AccessGrant(
            user_id=user_id, dataset_id=dataset.id, request_id=req.id,
            granted_at=now - timedelta(days=1), expires_at=now + timedelta(days=30),
        )
        db.add(grant)
        await db.flush()

        db.add(DownloadLog(grant_id=grant.id, user_id=user_id, downloaded_at=now))
        await db.commit()


class TestReportGeneration:
    @pytest.mark.parametrize(
        "report_type", ["usage_summary", "dataset_inventory", "user_activity", "access_grants"]
    )
    async def test_generate_each_report_type_becomes_ready(self, client, report_type):
        headers = await _admin_headers(client, permissions=["View Reports"])
        await _seed_report_fixture(client)

        r = await client.post("/api/v1/admin/reports", json={"type": report_type}, headers=headers)
        assert r.status_code == 201, r.text
        report_id = r.json()["id"]
        # Celery runs eagerly in tests (see conftest) — the generator has
        # already run synchronously by the time create_report returns, so
        # the report should already be ready without any polling.
        assert r.json()["ready"] is True

        r = await client.get(f"/api/v1/admin/reports/{report_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["ready"] is True

    async def test_list_shows_ready_report(self, client):
        headers = await _admin_headers(client, permissions=["View Reports"])
        await _seed_report_fixture(client)

        r = await client.post(
            "/api/v1/admin/reports", json={"type": "usage_summary"}, headers=headers
        )
        report_id = r.json()["id"]

        r = await client.get("/api/v1/admin/reports", headers=headers)
        assert r.status_code == 200
        report = next(rep for rep in r.json() if rep["id"] == report_id)
        assert report["ready"] is True

    async def test_download_returns_working_url(self, client):
        headers = await _admin_headers(client, permissions=["View Reports"])
        await _seed_report_fixture(client)

        r = await client.post(
            "/api/v1/admin/reports", json={"type": "dataset_inventory"}, headers=headers
        )
        report_id = r.json()["id"]

        r = await client.get(f"/api/v1/admin/reports/{report_id}/download", headers=headers)
        assert r.status_code == 200, r.text
        url = r.json()["download_url"]
        assert url.startswith("http")

        import urllib.request

        with urllib.request.urlopen(url) as resp:
            content = resp.read()
        assert b"Report Dataset" in content or b"Code" in content  # header row at minimum

    async def test_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.post(
            "/api/v1/admin/reports", json={"type": "usage_summary"}, headers=headers
        )
        assert r.status_code == 403

        r = await client.get("/api/v1/admin/reports", headers=headers)
        assert r.status_code == 403
