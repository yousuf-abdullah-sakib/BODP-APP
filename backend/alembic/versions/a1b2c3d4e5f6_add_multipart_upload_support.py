"""add multipart upload support to uploads table

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-08-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All-nullable additive columns — the existing single-request small-file
    # upload path never populates any of these (Phase 1 plan §"What must
    # NOT change": that path's contract is untouched).
    op.add_column('uploads', sa.Column('multipart_upload_id', sa.String(length=255), nullable=True))
    op.add_column('uploads', sa.Column('storage_bucket', sa.String(length=255), nullable=True))
    op.add_column('uploads', sa.Column('storage_key', sa.String(length=1024), nullable=True))
    op.add_column('uploads', sa.Column('total_size_bytes', sa.BigInteger(), nullable=True))
    op.add_column('uploads', sa.Column('total_parts', sa.Integer(), nullable=True))
    op.add_column(
        'uploads',
        sa.Column('uploaded_bytes', sa.BigInteger(), nullable=False, server_default='0'),
    )
    op.add_column('uploads', sa.Column('progress_stage', sa.String(length=50), nullable=True))
    op.add_column('uploads', sa.Column('progress_pct', sa.Integer(), nullable=True))

    # uploads.status is a plain VARCHAR(20), not a native Postgres ENUM
    # (matches the existing convention in this codebase — UploadStatus is a
    # StrEnum validated at the application layer, not a DB-level CHECK) —
    # so no migration step is needed to add the new 'initiated'/'cancelled'
    # values; they're just new strings the column already accepts.


def downgrade() -> None:
    op.drop_column('uploads', 'progress_pct')
    op.drop_column('uploads', 'progress_stage')
    op.drop_column('uploads', 'uploaded_bytes')
    op.drop_column('uploads', 'total_parts')
    op.drop_column('uploads', 'total_size_bytes')
    op.drop_column('uploads', 'storage_key')
    op.drop_column('uploads', 'storage_bucket')
    op.drop_column('uploads', 'multipart_upload_id')
