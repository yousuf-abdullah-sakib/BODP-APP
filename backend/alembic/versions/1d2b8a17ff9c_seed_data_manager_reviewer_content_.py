"""seed data manager reviewer content editor roles

Revision ID: 1d2b8a17ff9c
Revises: db95d2a1579d
Create Date: 2026-08-11 10:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = '1d2b8a17ff9c'
down_revision: Union[str, None] = 'db95d2a1579d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLES_TABLE = sa.table(
    "roles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("permissions", ARRAY(sa.String(64))),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

# Sensible starting permission sets matching each role's name — all three
# stay fully editable from Roles & Permissions afterward; this is only the
# seed default, not a fixed policy. Frozen-in-time literal, per every
# other seed migration in this project (never import live app.models).
_NEW_ROLES = [
    (
        "Data Manager",
        "Manages dataset content, storage, and reporting.",
        ["Edit Datasets", "Delete Datasets", "View Reports", "Manage Backups"],
    ),
    (
        "Reviewer",
        "Reviews and approves dataset access requests.",
        ["Approve Requests", "View Analytics", "View Audit Log"],
    ),
    (
        "Content Editor",
        "Manages public-facing site content.",
        ["Publish Content", "Manage CMS", "Manage Blog", "Manage Media"],
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    existing_names = set(bind.execute(sa.select(_ROLES_TABLE.c.name)).scalars().all())

    for name, description, permissions in _NEW_ROLES:
        if name not in existing_names:
            bind.execute(
                _ROLES_TABLE.insert().values(
                    id=uuid.uuid4(),
                    name=name,
                    description=description,
                    permissions=permissions,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    names = [name for name, _, _ in _NEW_ROLES]
    bind.execute(_ROLES_TABLE.delete().where(_ROLES_TABLE.c.name.in_(names)))
