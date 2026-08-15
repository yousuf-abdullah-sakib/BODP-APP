"""Admin filter-configuration review workflow (Catalog/Request follow-up
requirement) — proves the three-state distinction the feature exists to
guarantee:

  1. Original User Request  -> DatasetRequest.search_criteria (immutable)
  2. Admin Modified Config  -> DatasetRequest.admin_modified_search_criteria
  3. Final Approved Config  -> AccessGrant.scope

Specifically verifies: the original is never overwritten by Save Changes
or by Approve, admin_modified_search_criteria is genuinely separate and
persists independently of approval, approving without ever modifying
falls back to the original correctly (regression guard for existing
behavior), and approving after Save Changes uses the saved modification
by default."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.limiter import limiter
from app.models.requests import AccessGrant, DatasetRequest
from tests.conftest import register_verified_user
from tests.test_requests import (
    _LONG_JUSTIFICATION,
    _admin_headers_with_approve_permission,
    _researcher_headers,
    _seed_published_dataset,
    _submit_request,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    # This file's ~15 register_verified_user calls, combined with
    # test_requests.py/test_extraction.py's own registrations in the same
    # pytest session, exceed RATE_LIMIT_AUTH (10/minute) — matching
    # test_catalog.py's established fix for the identical issue.
    limiter.enabled = False


async def _submit_with_criteria(client, headers, dataset_id: str, criteria: dict) -> str:
    r = await _submit_request(
        client, headers, dataset_id, search_criteria=json.dumps(criteria)
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestModifyEndpointPermission:
    async def test_modify_requires_approve_requests_permission(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        request_id = await _submit_with_criteria(
            client, researcher_headers, dataset_id, {"category": "Environmental"}
        )

        no_perm_token = await register_verified_user(
            client, email="no-perm-admin@example.com", admin=True, permissions=["Manage Users"]
        )
        r = await client.patch(
            f"/api/v1/admin/requests/{request_id}/modify",
            json={"search_criteria": {"category": "Narrowed"}},
            headers={"Authorization": f"Bearer {no_perm_token}"},
        )
        assert r.status_code == 403


class TestOriginalNeverOverwritten:
    async def test_save_changes_does_not_touch_original_search_criteria(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        original = {"category": "Environmental", "parameters": ["VHM0"], "date_from": "2015-01-01"}
        request_id = await _submit_with_criteria(client, researcher_headers, dataset_id, original)

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.patch(
            f"/api/v1/admin/requests/{request_id}/modify",
            json={
                "search_criteria": {
                    "category": "Environmental",
                    "parameters": ["VHM0", "VTM10"],
                    "date_from": "2016-01-01",
                }
            },
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text

        async with AsyncSessionLocal() as db:
            request = await db.get(DatasetRequest, uuid.UUID(request_id))
            assert request.search_criteria["parameters"] == ["VHM0"]
            assert request.search_criteria["date_from"] == "2015-01-01"
            assert request.admin_modified_search_criteria["parameters"] == ["VHM0", "VTM10"]
            assert request.admin_modified_search_criteria["date_from"] == "2016-01-01"

    async def test_approve_with_override_does_not_touch_original_search_criteria(self, client):
        """Regression guard: the OLD behavior overwrote request.
        search_criteria with the admin's override at approval time — this
        proves that no longer happens, the override now lands in
        admin_modified_search_criteria instead."""
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        original = {"category": "Environmental"}
        request_id = await _submit_with_criteria(client, researcher_headers, dataset_id, original)

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y", "search_criteria": {"category": "Narrowed"}},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text

        async with AsyncSessionLocal() as db:
            request = await db.get(DatasetRequest, uuid.UUID(request_id))
            assert request.search_criteria == {"category": "Environmental"}
            assert request.admin_modified_search_criteria == {"category": "Narrowed"}


class TestModifyThenReviewLater:
    async def test_saved_modification_is_visible_on_request_detail(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        request_id = await _submit_with_criteria(
            client, researcher_headers, dataset_id, {"parameters": ["VHM0"]}
        )

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.patch(
            f"/api/v1/admin/requests/{request_id}/modify",
            json={"search_criteria": {"parameters": ["VHM0", "VTM10", "VSDX"]}},
            headers=admin_headers,
        )

        # A second admin (or the same one, later) reviewing the queue must
        # see the saved modification without having triggered it.
        r = await client.get("/api/v1/admin/requests", headers=admin_headers)
        assert r.status_code == 200
        matching = next(row for row in r.json() if row["id"] == request_id)
        assert matching["search_criteria"]["parameters"] == ["VHM0"]
        assert matching["admin_modified_search_criteria"]["parameters"] == [
            "VHM0",
            "VTM10",
            "VSDX",
        ]

    async def test_modify_does_not_change_request_status(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        request_id = await _submit_with_criteria(
            client, researcher_headers, dataset_id, {"category": "Environmental"}
        )

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.patch(
            f"/api/v1/admin/requests/{request_id}/modify",
            json={"search_criteria": {"category": "Narrowed"}},
            headers=admin_headers,
        )
        assert r.json()["status"] == "pending"

    async def test_cannot_modify_a_decided_request(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        request_id = await _submit_with_criteria(
            client, researcher_headers, dataset_id, {"category": "Environmental"}
        )

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )

        r = await client.patch(
            f"/api/v1/admin/requests/{request_id}/modify",
            json={"search_criteria": {"category": "TooLate"}},
            headers=admin_headers,
        )
        assert r.status_code == 409


class TestApprovalScopeSourcing:
    async def test_approve_without_modification_uses_original_as_grant_scope(self, client):
        """Regression guard: approving a request that was never modified
        must produce a grant scope identical to the original — matching
        the behavior that existed before admin_modified_search_criteria
        was introduced."""
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        original = {"category": "Environmental", "parameters": ["VHM0"]}
        request_id = await _submit_with_criteria(client, researcher_headers, dataset_id, original)

        admin_headers = await _admin_headers_with_approve_permission(client)
        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["scope"]["parameters"] == ["VHM0"]

    async def test_approve_after_save_changes_uses_saved_modification_as_grant_scope(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        request_id = await _submit_with_criteria(
            client, researcher_headers, dataset_id, {"parameters": ["VHM0"]}
        )

        admin_headers = await _admin_headers_with_approve_permission(client)
        modify_resp = await client.patch(
            f"/api/v1/admin/requests/{request_id}/modify",
            json={"search_criteria": {"parameters": ["VHM0", "VTM10"]}},
            headers=admin_headers,
        )
        assert modify_resp.status_code == 200, modify_resp.text

        approve_resp = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["scope"]["parameters"] == ["VHM0", "VTM10"]

        async with AsyncSessionLocal() as db:
            request = await db.get(DatasetRequest, uuid.UUID(request_id))
            # Original still exactly as submitted, even after modify + approve.
            assert request.search_criteria["parameters"] == ["VHM0"]

    async def test_approve_time_override_wins_over_previously_saved_modification(self, client):
        """An override passed directly to the approve call (e.g. approving
        straight from the modal without a prior Save Changes) takes
        precedence over an earlier saved modification, and is itself then
        recorded as the modification."""
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        request_id = await _submit_with_criteria(
            client, researcher_headers, dataset_id, {"parameters": ["VHM0"]}
        )

        admin_headers = await _admin_headers_with_approve_permission(client)
        await client.patch(
            f"/api/v1/admin/requests/{request_id}/modify",
            json={"search_criteria": {"parameters": ["VHM0", "VTM10"]}},
            headers=admin_headers,
        )

        r = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y", "search_criteria": {"parameters": ["VSDX"]}},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["scope"]["parameters"] == ["VSDX"]


class TestExistingApprovalRejectionUnaffected:
    """Explicit regression coverage: approve/reject must continue to work
    exactly as before for requests that never go through Save Changes at
    all — the new field and endpoint are purely additive."""

    async def test_approve_without_any_criteria_still_works(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        r = await _submit_request(client, researcher_headers, dataset_id)
        request_id = r.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        approve_resp = await client.post(
            f"/api/v1/admin/requests/{request_id}/approve",
            json={"duration": "1y"},
            headers=admin_headers,
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["scope"] is None

        async with AsyncSessionLocal() as db:
            grants = (
                await db.execute(
                    select(AccessGrant).where(AccessGrant.request_id == uuid.UUID(request_id))
                )
            ).scalars().all()
            assert len(grants) == 1

    async def test_reject_still_works(self, client):
        dataset_id = await _seed_published_dataset()
        researcher_headers = await _researcher_headers(client)
        r = await _submit_request(client, researcher_headers, dataset_id)
        request_id = r.json()["id"]

        admin_headers = await _admin_headers_with_approve_permission(client)
        reject_resp = await client.post(
            f"/api/v1/admin/requests/{request_id}/reject",
            json={"reason": "Insufficient justification detail."},
            headers=admin_headers,
        )
        assert reject_resp.status_code == 200, reject_resp.text
        assert reject_resp.json()["status"] == "rejected"
