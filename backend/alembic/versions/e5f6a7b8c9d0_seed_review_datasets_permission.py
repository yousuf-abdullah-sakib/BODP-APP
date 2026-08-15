"""seed review datasets permission

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-14 15:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen-in-time copy of app/models/user.py's PERMISSION_LIST as of just
# before this migration — a migration must never import live application
# code, only its own literal snapshot. See a3c47b895dae/29bbbb8a3f6b for
# the identical pattern this repeats.
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
    "Manage Support",
]

_NEW_PERMISSION = "Review Datasets"

_ROLES_TABLE = sa.table(
    "roles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("permissions", ARRAY(sa.String(64))),
)


def upgrade() -> None:
    # Any role whose permission set is EXACTLY the prior full 14 (i.e. the
    # seeded "Administrator" role, or any custom role an admin had already
    # brought up to full parity) gains "Review Datasets" too. Narrower
    # custom roles are left untouched until an admin explicitly edits them.
    # Idempotent: a role already holding all 15 no longer matches the
    # 14-item exact-set precondition, so replaying this migration is a
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
    # Strip "Review Datasets" from any role currently holding all 15,
    # restoring it to the prior 14. Roles that never got the new
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
