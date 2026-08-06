"""seed default roles

Revision ID: 6220672f5bff
Revises: 47574d3ca56a
Create Date: 2026-08-06 15:11:25.356960

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = '6220672f5bff'
down_revision: Union[str, None] = '47574d3ca56a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen-in-time copy of app/models/user.py's PERMISSION_LIST — a migration
# must never import live application code, only its own literal snapshot,
# so future edits to PERMISSION_LIST can't silently rewrite migration
# history or break this migration on replay against an old codebase.
_PERMISSION_LIST = [
    "Approve Requests",
    "Manage Users",
    "Edit Datasets",
    "Delete Datasets",
    "Publish Content",
    "Manage Roles",
    "View Analytics",
    "Manage Backups",
]

_ROLES_TABLE = sa.table(
    "roles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("permissions", ARRAY(sa.String(64))),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    # Idempotent: every fresh install (dev, test, staging, prod) always
    # ends up with these two system roles, but replaying this migration
    # against a database that already has them (e.g. manually created by
    # an admin before this migration existed) is a safe no-op per role.
    bind = op.get_bind()
    existing_names = set(bind.execute(sa.select(_ROLES_TABLE.c.name)).scalars().all())

    if "Administrator" not in existing_names:
        bind.execute(
            _ROLES_TABLE.insert().values(
                id=uuid.uuid4(),
                name="Administrator",
                description="Full system access — all permissions.",
                permissions=_PERMISSION_LIST,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    if "User" not in existing_names:
        bind.execute(
            _ROLES_TABLE.insert().values(
                id=uuid.uuid4(),
                name="User",
                description="Standard researcher account — no elevated permissions.",
                permissions=[],
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(_ROLES_TABLE.delete().where(_ROLES_TABLE.c.name.in_(["Administrator", "User"])))
