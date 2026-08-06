import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.user import PERMISSION_LIST, Role

pytestmark = pytest.mark.asyncio

# Frozen copy matching the migration's own frozen snapshot
# (backend/alembic/versions/a3c47b895dae_seed_phase9_permissions.py) — this
# test exercises the same exact-set matching logic the migration performs,
# without literally invoking alembic (this suite doesn't run migrations
# per-test; see conftest.py's truncate-based reset instead).
_ORIGINAL_PERMISSION_LIST = [
    "Approve Requests",
    "Manage Users",
    "Edit Datasets",
    "Delete Datasets",
    "Publish Content",
    "Manage Roles",
    "View Analytics",
    "Manage Backups",
]

_NEW_PERMISSIONS = [
    "Manage CMS",
    "Manage Blog",
    "Manage Media",
    "View Reports",
    "View Audit Log",
]


async def _run_migration_upgrade_logic(db) -> None:
    """Replicates a3c47b895dae's upgrade() exactly: any role whose
    permission set is EXACTLY the original 8 gains the 5 new permissions;
    narrower roles are left untouched."""
    original_set = set(_ORIGINAL_PERMISSION_LIST)
    result = await db.execute(select(Role))
    for role in result.scalars().all():
        if set(role.permissions or []) == original_set:
            role.permissions = list(role.permissions) + _NEW_PERMISSIONS
    await db.commit()


class TestPhase9PermissionMigrationLogic:
    async def test_full_access_role_gains_all_five_new_permissions(self):
        async with AsyncSessionLocal() as db:
            full_access_role = Role(
                name="Administrator", description="Full access", permissions=list(_ORIGINAL_PERMISSION_LIST)
            )
            narrow_role = Role(
                name="Reviewer", description="Narrow custom role", permissions=["View Analytics", "Approve Requests"]
            )
            db.add_all([full_access_role, narrow_role])
            await db.commit()
            full_access_id, narrow_id = full_access_role.id, narrow_role.id

        async with AsyncSessionLocal() as db:
            await _run_migration_upgrade_logic(db)

        async with AsyncSessionLocal() as db:
            full_access_role = await db.get(Role, full_access_id)
            narrow_role = await db.get(Role, narrow_id)

            assert set(full_access_role.permissions) == set(_ORIGINAL_PERMISSION_LIST) | set(_NEW_PERMISSIONS)
            for perm in _NEW_PERMISSIONS:
                assert perm in full_access_role.permissions

            # Narrower custom role is left untouched — it never held the
            # exact original 8-permission set, so it doesn't match.
            assert set(narrow_role.permissions) == {"View Analytics", "Approve Requests"}

    async def test_role_already_holding_all_13_is_a_safe_noop(self):
        """Idempotency check: a role already upgraded no longer matches the
        8-item exact-set precondition, so replaying the migration must not
        duplicate permissions or otherwise alter it."""
        async with AsyncSessionLocal() as db:
            already_full = Role(
                name="Already Upgraded",
                permissions=list(_ORIGINAL_PERMISSION_LIST) + list(_NEW_PERMISSIONS),
            )
            db.add(already_full)
            await db.commit()
            role_id = already_full.id

        async with AsyncSessionLocal() as db:
            await _run_migration_upgrade_logic(db)

        async with AsyncSessionLocal() as db:
            role = await db.get(Role, role_id)
            assert sorted(role.permissions) == sorted(_ORIGINAL_PERMISSION_LIST + _NEW_PERMISSIONS)


class TestPermissionListConsistency:
    async def test_permission_list_has_thirteen_entries_including_new_five(self):
        assert len(PERMISSION_LIST) == 13
        for perm in _NEW_PERMISSIONS:
            assert perm in PERMISSION_LIST
        for perm in _ORIGINAL_PERMISSION_LIST:
            assert perm in PERMISSION_LIST
