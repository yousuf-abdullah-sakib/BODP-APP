"""make dataset_records time/lat/lon nullable

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-14 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Relaxing NOT NULL never invalidates existing rows — every row written
    # so far already has all three populated, so this is a pure constraint
    # loosening, not a data migration. The btree index on `time` is
    # unaffected (Postgres indexes NULLs fine).
    op.alter_column('dataset_records', 'time', existing_type=sa.Date(), nullable=True)
    op.alter_column('dataset_records', 'lat', existing_type=sa.Numeric(9, 6), nullable=True)
    op.alter_column('dataset_records', 'lon', existing_type=sa.Numeric(9, 6), nullable=True)


def downgrade() -> None:
    # Would fail if any row written under the nullable regime actually has
    # a null in one of these columns — that's intentional: a blind
    # downgrade must not silently invent fake time/lat/lon values to
    # satisfy a reinstated NOT NULL constraint.
    op.alter_column('dataset_records', 'lon', existing_type=sa.Numeric(9, 6), nullable=False)
    op.alter_column('dataset_records', 'lat', existing_type=sa.Numeric(9, 6), nullable=False)
    op.alter_column('dataset_records', 'time', existing_type=sa.Date(), nullable=False)
