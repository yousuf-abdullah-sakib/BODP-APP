"""add snapshot_version to datasets

Revision ID: a3b4c5d6e7f8
Revises: f7a8b9c0d1e2
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill — NULL correctly means "no snapshot generated
    # yet" for every existing row, which is exactly the state a dataset
    # ingested before this feature existed is actually in. A read
    # endpoint's staleness check (snapshot_version == version) already
    # treats NULL as "never matches", so this is a purely additive
    # column that changes no existing row's observed behavior.
    op.add_column('datasets', sa.Column('snapshot_version', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('datasets', 'snapshot_version')
