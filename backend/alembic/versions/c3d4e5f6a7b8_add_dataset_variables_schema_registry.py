"""add dataset_variables schema registry table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-14 14:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dataset_variables',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('dataset_id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('data_type', sa.String(length=20), nullable=False, server_default='text'),
        sa.Column('unit', sa.String(length=50), nullable=True),
        sa.Column('is_dimension', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('roles', ARRAY(sa.String(length=30)), nullable=False, server_default='{}'),
        sa.Column('min_value', sa.Numeric(18, 6), nullable=True),
        sa.Column('max_value', sa.Numeric(18, 6), nullable=True),
        sa.Column('distinct_values', ARRAY(sa.String(length=255)), nullable=True),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('dataset_id', 'name', name='uq_dataset_variables_dataset_id_name'),
    )
    op.create_index('ix_dataset_variables_dataset_id', 'dataset_variables', ['dataset_id'])


def downgrade() -> None:
    op.drop_index('ix_dataset_variables_dataset_id', table_name='dataset_variables')
    op.drop_table('dataset_variables')
