import pytest

from app.core.database import AsyncSessionLocal
from app.models.catalog import Dataset, DatasetCategory, DatasetStatus
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


async def _admin_headers(client, *, permissions: list[str] | None = None) -> dict:
    token = await register_verified_user(
        client, email="notif-admin@example.com", admin=True, permissions=permissions
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_published_dataset() -> str:
    async with AsyncSessionLocal() as db:
        category = DatasetCategory(name="Notif-Cat", description="test", color_tag="cat-Environmental")
        db.add(category)
        await db.flush()
        dataset = Dataset(
            code="BD-NOTIF-1",
            title="Notification Test Dataset",
            category_id=category.id,
            status=DatasetStatus.PUBLISHED.value,
            record_count=0,
        )
        db.add(dataset)
        await db.commit()
        await db.refresh(dataset)
        return str(dataset.id)


class TestRequestSubmittedNotifiesAdmins:
    async def test_admin_receives_notification(self, client):
        admin_headers = await _admin_headers(client)
        dataset_id = await _seed_published_dataset()

        researcher_token = await register_verified_user(client, email="notif-researcher@example.com")
        r = await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": "x" * 60},
            headers={"Authorization": f"Bearer {researcher_token}"},
        )
        assert r.status_code == 201, r.text

        r = await client.get("/api/v1/me/notifications", headers=admin_headers)
        assert r.status_code == 200
        assert any("Notification Test Dataset" in (n["description"] or "") for n in r.json())

    async def test_non_admin_notifications_unaffected(self, client):
        await _admin_headers(client)
        dataset_id = await _seed_published_dataset()

        researcher_token = await register_verified_user(client, email="notif-researcher2@example.com")
        researcher_headers = {"Authorization": f"Bearer {researcher_token}"}
        await client.post(
            "/api/v1/requests",
            data={"dataset_id": dataset_id, "justification": "x" * 60},
            headers=researcher_headers,
        )

        r = await client.get("/api/v1/me/notifications", headers=researcher_headers)
        assert r.status_code == 200
        assert r.json() == []


class TestUserRegisteredNotifiesAdmins:
    async def test_admin_receives_notification_on_new_registration(self, client):
        admin_headers = await _admin_headers(client)

        r = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "New Registrant",
                "email": "new-registrant@example.com",
                "password": "testpass123",
                "institution": None,
                "phone": None,
            },
        )
        assert r.status_code == 201

        r = await client.get("/api/v1/me/notifications", headers=admin_headers)
        assert r.status_code == 200
        assert any("New Registrant" in (n["description"] or "") for n in r.json())
