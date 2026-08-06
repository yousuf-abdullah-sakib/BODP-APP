import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.audit import AuditLogEntry
from app.models.catalog import Dataset, DatasetCategory, DatasetStatus
from app.models.requests import AccessGrant, DatasetRequest, GrantStatus, RequestStatus
from app.models.user import User
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio

_LONG_JUSTIFICATION = (
    "I am researching coastal water quality trends and need this dataset "
    "to calibrate my model against real observational records."
)


async def _seed_published_dataset(code: str = "BD-REQ-TEST") -> str:
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name=f"Cat-{code}", description="test", color_tag="cat-Environmental")
        db.add(category)
        await db.flush()
        dataset = Dataset(
            code=code,
            title="Request Test Dataset",
            description="test",
            category_id=category.id,
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.commit()
        await db.refresh(dataset)
        return str(dataset.id)


async def _admin_headers_with_approve_permission(client) -> dict:
    token = await register_verified_user(
        client,
        email="requests-admin@example.com",
        admin=True,
        permissions=["Approve Requests"],
    )
    return {"Authorization": f"Bearer {token}"}


async def _researcher_headers(client, email: str = "researcher@example.com") -> dict:
    token = await register_verified_user(client, email=email)
    return {"Authorization": f"Bearer {token}"}


async def _submit_request(client, headers, dataset_id: str, **overrides) -> dict:
    data = {"dataset_id": dataset_id, "justification": _LONG_JUSTIFICATION}
    data.update(overrides)
    r = await client.post("/api/v1/requests", data=data, headers=headers)
    return r


class TestSubmitRequest:
    async def test_submit_happy_path(self, client):
        dataset_id = await _seed_published_dataset()
        headers = await _researcher_headers(client)
        r = await _submit_request(client, headers, dataset_id)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "pending"
        assert body["dataset"]["id"] == dataset_id

    async def test_submit_requires_auth(self, client):
        dataset_id = await _seed_published_dataset()
        r = await _submit_request(client, {}, dataset_id)
        assert r.status_code == 401

    async def test_submit_justification_too_short_rejected(self, client):
        dataset_id = await _seed_published_dataset()
        headers = await _researcher_headers(client)
        r = await _submit_request(client, headers, dataset_id, justification="too short")
        assert r.status_code == 422

    async def test_submit_nonexistent_dataset_404s(self, client):
        headers = await _researcher_headers(client)
        r = await _submit_request(client, headers, str(uuid.uuid4()))
        assert r.status_code == 404

    async def test_submit_unpublished_dataset_404s(self, client):
        async with AsyncSessionLocal() as db:
            dataset = Dataset(
                code="BD-DRAFT-REQ",
                title="Draft dataset",
                status=DatasetStatus.DRAFT.value,
                record_count=0,
            )
            db.add(dataset)
            await db.commit()
            await db.refresh(dataset)
            dataset_id = str(dataset.id)

        headers = await _researcher_headers(client)
        r = await _submit_request(client, headers, dataset_id)
        assert r.status_code == 404

    async def test_submit_with_search_criteria(self, client):
        import json

        dataset_id = await _seed_published_dataset()
        headers = await _researcher_headers(client)
        criteria = json.dumps({"category": "Environmental", "date_from": "2024-01-01"})
        r = await _submit_request(client, headers, dataset_id, search_criteria=criteria)
        assert r.status_code == 201, r.text
        assert r.json()["search_criteria"]["category"] == "Environmental"


class TestAdminRequestsList:
    async def test_list_requires_approve_permission(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        await _submit_request(client, researcher_headers, dataset_id)

        # Plain admin without the "Approve Requests" role permission.
        token = await register_verified_user(client, email="plain-admin@example.com", admin=True)
        r = await client.get("/api/v1/admin/requests", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    async def test_list_non_admin_403s(self, client):
        headers = await _researcher_headers(client, email="rando@example.com")
        r = await client.get("/api/v1/admin/requests", headers=headers)
        assert r.status_code == 403

    async def test_list_returns_requester_identity(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        await _submit_request(client, researcher_headers, dataset_id)

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.get("/api/v1/admin/requests", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["user"]["email"] == "researcher@example.com"

    async def test_list_filters_by_status(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        await _submit_request(client, researcher_headers, dataset_id)

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.get("/api/v1/admin/requests?status=approved", headers=admin_headers)
        assert r.status_code == 200
        assert r.json() == []


class TestApproveRequest:
    async def test_approve_all_creates_grant(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        grant = r.json()
        assert grant["status"] == "active"
        assert grant["dataset"]["id"] == dataset_id

    async def test_approve_increments_datasets_granted(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "researcher@example.com"))
            ).scalar_one()
            assert user.datasets_granted == 1

    async def test_approve_writes_audit_log(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )

        async with AsyncSessionLocal() as db:
            entries = (await db.execute(select(AuditLogEntry))).scalars().all()
            assert any(e.action_type == "approve" for e in entries)

    async def test_approve_with_criteria_override(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y", "search_criteria": {"category": "Narrowed"}, "note": "scope narrowed"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()["scope"]["category"] == "Narrowed"

    async def test_cannot_approve_already_approved(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        assert r.status_code == 409

    @pytest.mark.parametrize(
        "duration,expected_days",
        [("5d", 5), ("10d", 10)],
    )
    async def test_approve_duration_days(self, client, duration, expected_days):
        dataset_id = await _seed_published_dataset(code=f"BD-DUR-{duration}")
        researcher_headers = await _researcher_headers(client, email=f"researcher-{duration}@example.com")
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        before = datetime.now(UTC)
        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": duration},
            headers=admin_headers,
        )
        expires_at = datetime.fromisoformat(r.json()["expires_at"])
        delta = expires_at - before
        assert timedelta(days=expected_days) - timedelta(minutes=1) <= delta <= timedelta(
            days=expected_days
        ) + timedelta(minutes=1)

    async def test_approve_custom_date(self, client):
        dataset_id = await _seed_published_dataset(code="BD-DUR-custom")
        researcher_headers = await _researcher_headers(client, email="researcher-custom@example.com")
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "custom", "custom_expires_at": "2030-06-15"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"].startswith("2030-06-15")

    async def test_approve_custom_without_date_rejected(self, client):
        dataset_id = await _seed_published_dataset(code="BD-DUR-custom-missing")
        researcher_headers = await _researcher_headers(client, email="researcher-custom2@example.com")
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "custom"},
            headers=admin_headers,
        )
        assert r.status_code == 422


class TestRejectRequest:
    async def test_reject_requires_reason(self, client):
        dataset_id = await _seed_published_dataset(code="BD-REJ-1")
        researcher_headers = await _researcher_headers(client, email="researcher-rej1@example.com")
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/reject", json={"reason": ""}, headers=admin_headers
        )
        assert r.status_code == 422

    async def test_reject_happy_path(self, client):
        dataset_id = await _seed_published_dataset(code="BD-REJ-2")
        researcher_headers = await _researcher_headers(client, email="researcher-rej2@example.com")
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/reject",
            json={"reason": "Justification insufficient."},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        assert r.json()["admin_note"] == "Justification insufficient."

    async def test_rejected_request_cannot_be_approved(self, client):
        dataset_id = await _seed_published_dataset(code="BD-REJ-3")
        researcher_headers = await _researcher_headers(client, email="researcher-rej3@example.com")
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.post(
            f"/api/v1/admin/requests/{request_id}/reject",
            json={"reason": "no"},
            headers=admin_headers,
        )
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        assert r.status_code == 409

    async def test_reject_writes_audit_log(self, client):
        dataset_id = await _seed_published_dataset(code="BD-REJ-4")
        researcher_headers = await _researcher_headers(client, email="researcher-rej4@example.com")
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.post(
            f"/api/v1/admin/requests/{request_id}/reject",
            json={"reason": "no"},
            headers=admin_headers,
        )

        async with AsyncSessionLocal() as db:
            entries = (await db.execute(select(AuditLogEntry))).scalars().all()
            assert any(e.action_type == "reject" for e in entries)


class TestExtendAndRevokeGrant:
    async def _approved_grant(self, client, *, code_suffix: str, initial_duration: str = "5d"):
        dataset_id = await _seed_published_dataset(code=f"BD-GR-{code_suffix}")
        researcher_headers = await _researcher_headers(client, email=f"researcher-{code_suffix}@example.com")
        submit_resp = await _submit_request(client, researcher_headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        approve_resp = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": initial_duration},
            headers=admin_headers,
        )
        return approve_resp.json(), admin_headers, researcher_headers

    async def test_extend_recomputes_from_current_expiry_not_today(self, client):
        grant, admin_headers, _ = await self._approved_grant(client, code_suffix="ext1", initial_duration="5d")
        grant_id = grant["id"]
        original_expiry = datetime.fromisoformat(grant["expires_at"])

        r = await client.post(
            f"/api/v1/admin/grants/{grant_id}/extend",
            json={"duration": "1m"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        new_expiry = datetime.fromisoformat(r.json()["expires_at"])

        # Must be original_expiry + 1 month, NOT today + 1 month — the one
        # explicit "preserve this exact prototype behavior" requirement
        # (Master Plan §3 Phase 4 task 6). original_expiry was ~5 days from
        # now, so "from today" vs "from current expiry" differ by ~5 days —
        # assert against the exact relativedelta the implementation uses.
        from dateutil.relativedelta import relativedelta

        expected = original_expiry + relativedelta(months=1)
        assert abs((new_expiry - expected).total_seconds()) < 5

        incorrect_from_today = datetime.now(UTC) + relativedelta(months=1)
        assert abs((new_expiry - incorrect_from_today).total_seconds()) > 3600

    async def test_extend_requires_active_grant(self, client):
        grant, admin_headers, _ = await self._approved_grant(client, code_suffix="ext2")
        grant_id = grant["id"]
        await client.post(f"/api/v1/admin/grants/{grant_id}/revoke", headers=admin_headers)

        r = await client.post(
            f"/api/v1/admin/grants/{grant_id}/extend",
            json={"duration": "1m"},
            headers=admin_headers,
        )
        assert r.status_code == 409

    async def test_revoke_flips_status_and_decrements_count(self, client):
        grant, admin_headers, _ = await self._approved_grant(client, code_suffix="rev1")
        grant_id = grant["id"]

        r = await client.post(f"/api/v1/admin/grants/{grant_id}/revoke", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "revoked"

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == "researcher-rev1@example.com"))
            ).scalar_one()
            assert user.datasets_granted == 0

    async def test_revoke_writes_audit_log(self, client):
        grant, admin_headers, _ = await self._approved_grant(client, code_suffix="rev2")
        grant_id = grant["id"]
        await client.post(f"/api/v1/admin/grants/{grant_id}/revoke", headers=admin_headers)

        async with AsyncSessionLocal() as db:
            entries = (await db.execute(select(AuditLogEntry))).scalars().all()
            assert any(e.action_type == "revoke" for e in entries)

    async def test_cannot_revoke_already_revoked(self, client):
        grant, admin_headers, _ = await self._approved_grant(client, code_suffix="rev3")
        grant_id = grant["id"]
        await client.post(f"/api/v1/admin/grants/{grant_id}/revoke", headers=admin_headers)
        r = await client.post(f"/api/v1/admin/grants/{grant_id}/revoke", headers=admin_headers)
        assert r.status_code == 409


class TestMeEndpoints:
    async def test_me_requests_scoped_to_user(self, client):
        dataset_id = await _seed_published_dataset(code="BD-ME-1")
        headers_a = await _researcher_headers(client, email="me-a@example.com")
        headers_b = await _researcher_headers(client, email="me-b@example.com")
        await _submit_request(client, headers_a, dataset_id)

        r_a = await client.get("/api/v1/me/requests", headers=headers_a)
        r_b = await client.get("/api/v1/me/requests", headers=headers_b)
        assert len(r_a.json()) == 1
        assert len(r_b.json()) == 0

    async def test_me_grants_scoped_to_user(self, client):
        dataset_id = await _seed_published_dataset(code="BD-ME-2")
        headers_a = await _researcher_headers(client, email="me-grant-a@example.com")
        submit_resp = await _submit_request(client, headers_a, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )

        headers_b = await _researcher_headers(client, email="me-grant-b@example.com")
        r_a = await client.get("/api/v1/me/grants", headers=headers_a)
        r_b = await client.get("/api/v1/me/grants", headers=headers_b)
        assert len(r_a.json()) == 1
        assert len(r_b.json()) == 0


class TestAdminGrantsList:
    async def test_list_grants_requires_permission(self, client):
        headers = await _researcher_headers(client, email="grants-nonadmin@example.com")
        r = await client.get("/api/v1/admin/grants", headers=headers)
        assert r.status_code == 403

    async def test_list_grants_filters_by_status(self, client):
        dataset_id = await _seed_published_dataset(code="BD-ADMGR-1")
        headers = await _researcher_headers(client, email="admgr-a@example.com")
        submit_resp = await _submit_request(client, headers, dataset_id)
        request_id = submit_resp.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )

        r = await client.get("/api/v1/admin/grants?status=revoked", headers=admin_headers)
        assert r.status_code == 200
        assert r.json() == []

        r_active = await client.get("/api/v1/admin/grants?status=active", headers=admin_headers)
        assert len(r_active.json()) == 1
        assert r_active.json()[0]["granted_by_name"] is not None
