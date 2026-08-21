"""wire up backups table (Phase 10.3)

Revision ID: 703e3d8433c7
Revises: a3b4c5d6e7f8
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '703e3d8433c7'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The `backups` table itself already exists (initial schema revision
    # 441fedb081f0) but has sat completely unused — this migration adds
    # the columns Phase 10.3 needs to actually wire it up, mirroring the
    # same status/celery_task_id/error_message/completed_at shape
    # SubsetExtraction and VisualizationJob already use for real
    # Celery-job tracking, which the original table (started_at/
    # size_bytes/status/storage_key only, no in-progress tracking) never
    # had. All nullable — no backfill needed, existing rows (there are
    # none, since nothing has ever written to this table) are unaffected
    # either way.
    op.add_column('backups', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('backups', sa.Column('celery_task_id', sa.String(length=255), nullable=True))
    op.add_column('backups', sa.Column('error_message', sa.String(length=2000), nullable=True))
    # Row counts for a handful of core tables, captured at dump time —
    # see Backup.row_counts's own docstring in models/admin.py for why
    # this must be captured at dump time rather than compared against a
    # live query later (the live database is a moving target, making a
    # live-vs-restored comparison inherently racy against any write
    # between dump and verification).
    op.add_column(
        'backups',
        sa.Column('row_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('backups', 'row_counts')
    op.drop_column('backups', 'error_message')
    op.drop_column('backups', 'celery_task_id')
    op.drop_column('backups', 'completed_at')
