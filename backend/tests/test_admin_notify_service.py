import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.notifications import Notification
from app.models.user import Role, User, UserRole
from app.services.admin_notify_service import notify_admins_with_permission
from tests.conftest import register_verified_user

pytestmark = pytest.mark.asyncio


class TestNotifyAdminsWithPermission:
    async def test_selects_by_fine_grained_permission_not_coarse_role(self, client):
        # Shared infra used by both the contact-form and support-ticket
        # features this session, and directly related to the
        # double-notification bug fixed earlier for request submissions
        # (see test_requests.py::test_submit_notifies_approving_admin_exactly_once
        # and this function's own docstring in admin_notify_service.py).
        #
        # Seed one user with the target permission via a custom Role and one
        # user with a DIFFERENT permission via a different custom Role.
        # Neither is granted coarse role=='admin' here — notify_admins_with_permission
        # is documented to select purely by Role.permissions.any(permission),
        # unlike notify_admins' coarse role=='admin' broadcast, so this also
        # verifies it does NOT implicitly require the coarse admin role.
        target_email = "notify-target@example.com"
        other_email = "notify-other@example.com"
        await register_verified_user(client, email=target_email)
        await register_verified_user(client, email=other_email)

        async with AsyncSessionLocal() as db:
            target_user = (
                await db.execute(select(User).where(User.email == target_email))
            ).scalar_one()
            other_user = (
                await db.execute(select(User).where(User.email == other_email))
            ).scalar_one()

            target_role = Role(
                name="Support Role", description="test", permissions=["Manage Support"]
            )
            other_role = Role(
                name="Other Role", description="test", permissions=["Edit Datasets"]
            )
            db.add_all([target_role, other_role])
            await db.flush()
            db.add(UserRole(user_id=target_user.id, role_id=target_role.id))
            db.add(UserRole(user_id=other_user.id, role_id=other_role.id))
            await db.commit()

            await notify_admins_with_permission(
                db,
                permission="Manage Support",
                type="info",
                title="Unit test notification",
                description="testing selection by permission",
            )
            await db.commit()

            target_notifs = (
                await db.execute(
                    select(Notification).where(Notification.user_id == target_user.id)
                )
            ).scalars().all()
            other_notifs = (
                await db.execute(
                    select(Notification).where(Notification.user_id == other_user.id)
                )
            ).scalars().all()

        assert len(target_notifs) == 1
        assert target_notifs[0].title == "Unit test notification"
        assert other_notifs == []

    async def test_one_row_per_matching_admin_no_duplicates_for_multiple_roles(self, client):
        # A user holding TWO roles that both grant the same permission must
        # still get exactly one Notification row, not one per matching role
        # — this is what the `.distinct()` in the query is for.
        email = "notify-multi-role@example.com"
        await register_verified_user(client, email=email)

        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one()
            role_a = Role(name="Role A", description="test", permissions=["Manage Support"])
            role_b = Role(
                name="Role B", description="test", permissions=["Manage Support", "Edit Datasets"]
            )
            db.add_all([role_a, role_b])
            await db.flush()
            db.add(UserRole(user_id=user.id, role_id=role_a.id))
            db.add(UserRole(user_id=user.id, role_id=role_b.id))
            await db.commit()

            await notify_admins_with_permission(
                db,
                permission="Manage Support",
                type="info",
                title="Multi-role dedup test",
                description="should appear once",
            )
            await db.commit()

            notifs = (
                await db.execute(select(Notification).where(Notification.user_id == user.id))
            ).scalars().all()

        assert len(notifs) == 1
