"""seed manage support permission

Revision ID: 29bbbb8a3f6b
Revises: 43bb4fc6ec98
Create Date: 2026-08-11 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = '29bbbb8a3f6b'
down_revision: Union[str, None] = '43bb4fc6ec98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen-in-time copy of app/models/user.py's PERMISSION_LIST as of just
# before this migration — a migration must never import live application
# code, only its own literal snapshot. See a3c47b895dae for the identical
# pattern this repeats.
_PRIOR_PERMISSION_LIST = [
    "Approve Requests",
    "Manage Users",
    "Edit Datasets",
    "Delete Datasets",
    "Publish Content",
    "Manage Roles",
    "View Analytics",
    "Manage Backups",
    "Manage CMS",
    "Manage Blog",
    "Manage Media",
    "View Reports",
    "View Audit Log",
]

_NEW_PERMISSION = "Manage Support"

_ROLES_TABLE = sa.table(
    "roles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("permissions", ARRAY(sa.String(64))),
)


def upgrade() -> None:
    # Any role whose permission set is EXACTLY the prior full 13 (i.e. the
    # seeded "Administrator" role, or any custom role an admin had already
    # brought up to full parity) gains "Manage Support" too. Narrower
    # custom roles are left untouched until an admin explicitly edits them.
    # Idempotent: a role already holding all 14 no longer matches the
    # 13-item exact-set precondition, so replaying this migration is a
    # safe no-op.
    bind = op.get_bind()
    prior_set = set(_PRIOR_PERMISSION_LIST)

    rows = bind.execute(sa.select(_ROLES_TABLE.c.id, _ROLES_TABLE.c.permissions)).all()
    for role_id, permissions in rows:
        if set(permissions or []) == prior_set:
            bind.execute(
                _ROLES_TABLE.update()
                .where(_ROLES_TABLE.c.id == role_id)
                .values(permissions=list(permissions) + [_NEW_PERMISSION])
            )


def downgrade() -> None:
    # Strip "Manage Support" from any role currently holding all 14,
    # restoring it to the prior 13. Roles that never got the new
    # permission (narrower custom roles) are untouched in either direction.
    bind = op.get_bind()
    full_set = set(_PRIOR_PERMISSION_LIST) | {_NEW_PERMISSION}

    rows = bind.execute(sa.select(_ROLES_TABLE.c.id, _ROLES_TABLE.c.permissions)).all()
    for role_id, permissions in rows:
        if set(permissions or []) == full_set:
            bind.execute(
                _ROLES_TABLE.update()
                .where(_ROLES_TABLE.c.id == role_id)
                .values(permissions=_PRIOR_PERMISSION_LIST)
            )
