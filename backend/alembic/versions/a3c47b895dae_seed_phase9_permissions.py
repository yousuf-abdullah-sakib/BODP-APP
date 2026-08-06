"""seed phase9 permissions

Revision ID: a3c47b895dae
Revises: 6220672f5bff
Create Date: 2026-08-06 19:21:33.342057

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = 'a3c47b895dae'
down_revision: Union[str, None] = '6220672f5bff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen-in-time copies — a migration must never import live application
# code, only its own literal snapshot, so future edits to PERMISSION_LIST
# can't silently rewrite migration history or break this migration on
# replay against an old codebase.
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

_ROLES_TABLE = sa.table(
    "roles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("permissions", ARRAY(sa.String(64))),
)


def upgrade() -> None:
    # Any role whose permission set is EXACTLY the original 8 (full-access
    # intent, e.g. the seeded "Administrator" role) gains the 5 new Phase 9
    # permissions too. Narrower custom roles are left untouched until an
    # admin explicitly edits them. Idempotent: a role already holding all
    # 13 no longer matches the 8-item exact-set precondition, so replaying
    # this migration is a safe no-op.
    bind = op.get_bind()
    original_set = set(_ORIGINAL_PERMISSION_LIST)

    rows = bind.execute(sa.select(_ROLES_TABLE.c.id, _ROLES_TABLE.c.permissions)).all()
    for role_id, permissions in rows:
        if set(permissions or []) == original_set:
            bind.execute(
                _ROLES_TABLE.update()
                .where(_ROLES_TABLE.c.id == role_id)
                .values(permissions=list(permissions) + _NEW_PERMISSIONS)
            )


def downgrade() -> None:
    # Strip exactly the 5 new permissions from any role currently holding
    # all 13 (original 8 + new 5), restoring it to the original 8. Roles
    # that never got the new permissions (narrower custom roles) are
    # untouched in either direction.
    bind = op.get_bind()
    full_set = set(_ORIGINAL_PERMISSION_LIST) | set(_NEW_PERMISSIONS)

    rows = bind.execute(sa.select(_ROLES_TABLE.c.id, _ROLES_TABLE.c.permissions)).all()
    for role_id, permissions in rows:
        if set(permissions or []) == full_set:
            bind.execute(
                _ROLES_TABLE.update()
                .where(_ROLES_TABLE.c.id == role_id)
                .values(permissions=_ORIGINAL_PERMISSION_LIST)
            )
