"""add actor_email to audit_log

Revision ID: f1a2b3c4d5e6
Revises: c23aca66d242
Create Date: 2026-08-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'c23aca66d242'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUDIT_LOG_TABLE = sa.table(
    "audit_log",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("actor_id", UUID(as_uuid=True)),
    sa.column("actor_email", sa.String),
)
_USERS_TABLE = sa.table("users", sa.column("id", UUID(as_uuid=True)), sa.column("email", sa.String))


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("actor_email", sa.String(length=320), nullable=True))

    # Backfill existing rows from the still-live actor_id -> users FK so
    # historical entries display an Actor Email too, not just ones written
    # after this migration. Rows whose actor was since deleted (actor_id
    # NULL, per the FK's ondelete="SET NULL") are left NULL — same as
    # actor_name already tolerates for deleted actors.
    bind = op.get_bind()
    bind.execute(
        _AUDIT_LOG_TABLE.update()
        .values(
            actor_email=sa.select(_USERS_TABLE.c.email)
            .where(_USERS_TABLE.c.id == _AUDIT_LOG_TABLE.c.actor_id)
            .scalar_subquery()
        )
        .where(_AUDIT_LOG_TABLE.c.actor_id.is_not(None))
    )


def downgrade() -> None:
    op.drop_column("audit_log", "actor_email")
