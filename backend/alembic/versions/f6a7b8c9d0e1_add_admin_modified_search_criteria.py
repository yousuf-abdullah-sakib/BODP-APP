"""add admin_modified_search_criteria to dataset_requests

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-15 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill needed — absence correctly means "an admin
    # hasn't modified this request's filter configuration," which is the
    # right interpretation for every pre-existing row. The original
    # search_criteria column is untouched by this migration; this is a
    # purely additive column supporting a genuinely separate state.
    op.add_column(
        'dataset_requests',
        sa.Column('admin_modified_search_criteria', JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dataset_requests', 'admin_modified_search_criteria')
