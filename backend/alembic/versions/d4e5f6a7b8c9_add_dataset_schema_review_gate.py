"""add dataset schema review gate

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-14 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Orthogonal to Upload.status (Phase 1) — nullable, purely additive.
    # Null means "not yet reviewed"; no backfill, existing datasets simply
    # show as pending review until an admin visits the new screen.
    op.add_column('datasets', sa.Column('schema_reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('datasets', sa.Column('schema_reviewed_by', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_datasets_schema_reviewed_by_users',
        'datasets', 'users',
        ['schema_reviewed_by'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_datasets_schema_reviewed_by_users', 'datasets', type_='foreignkey')
    op.drop_column('datasets', 'schema_reviewed_by')
    op.drop_column('datasets', 'schema_reviewed_at')
