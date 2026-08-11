import csv
import io

import pytest

from app.core.database import AsyncSessionLocal
from app.models.audit import AuditActionType, AuditLogEntry
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str]) -> dict:
    slug = "-".join(p.replace(" ", "").lower() for p in permissions)
    token = await register_verified_user(
        client, email=f"audit-admin-{slug}@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_entries() -> None:
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                AuditLogEntry(
                    actor_name="Alice Admin", action="Approved request",
                    action_type=AuditActionType.APPROVE.value, target="Dataset A",
                ),
                AuditLogEntry(
                    actor_name="Alice Admin", action="Rejected request",
                    action_type=AuditActionType.REJECT.value, target="Dataset B",
                ),
                AuditLogEntry(
                    actor_name="Bob Admin", action="Created blog post",
                    action_type=AuditActionType.CONTENT.value, target="My Post",
                ),
                AuditLogEntry(
                    actor_name="Bob Admin", action="Updated CMS block",
                    action_type=AuditActionType.CONTENT.value, target="home/hero",
                ),
                AuditLogEntry(
                    actor_name="Carol Admin", action="User logged in",
                    action_type=AuditActionType.LOGIN.value, target=None,
                ),
            ]
        )
        await db.commit()


class TestAuditListPagination:
    async def test_list_and_paginate(self, client):
        headers = await _admin_headers(client, permissions=["View Audit Log"])
        await _seed_entries()

        r = await client.get("/api/v1/admin/audit-log", params={"page": 1, "page_size": 2}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["page"] == 1
        assert body["page_size"] == 2

        r = await client.get("/api/v1/admin/audit-log", params={"page": 3, "page_size": 2}, headers=headers)
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1

    async def test_filter_by_action_type(self, client):
        headers = await _admin_headers(client, permissions=["View Audit Log"])
        await _seed_entries()

        r = await client.get(
            "/api/v1/admin/audit-log", params={"action_type": "content"}, headers=headers
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert all(item["action_type"] == "content" for item in body["items"])

    async def test_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.get("/api/v1/admin/audit-log", headers=headers)
        assert r.status_code == 403


class TestAuditExportMatchesFilter:
    async def test_csv_export_only_contains_filtered_rows(self, client):
        """Quality-check requirement: list and export share one _build_query
        function — verify the CSV body for a filtered export contains only
        rows matching that filter, never the full unfiltered set."""
        headers = await _admin_headers(client, permissions=["View Audit Log"])
        await _seed_entries()

        r = await client.get(
            "/api/v1/admin/audit-log/export", params={"action_type": "content"}, headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")

        reader = csv.reader(io.StringIO(r.text))
        rows = list(reader)
        header, data_rows = rows[0], rows[1:]
        assert header == ["Time", "Actor", "Actor Email", "Action", "Target", "Type", "IP Address"]

        assert len(data_rows) == 2
        type_col = header.index("Type")
        assert all(row[type_col] == "content" for row in data_rows)

        actions = {row[header.index("Action")] for row in data_rows}
        assert actions == {"Created blog post", "Updated CMS block"}
        assert "Approved request" not in actions
        assert "User logged in" not in actions

    async def test_export_requires_permission(self, client):
        headers = await _admin_headers(client, permissions=["Manage Blog"])
        r = await client.get("/api/v1/admin/audit-log/export", headers=headers)
        assert r.status_code == 403
