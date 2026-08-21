"""add request_supporting_documents table and repoint supporting_document fk

Revision ID: f7a8b9c0d1e2
Revises: 9d56d5e03e87
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = '9d56d5e03e87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'request_supporting_documents',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('request_id', UUID(as_uuid=True), sa.ForeignKey('dataset_requests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('uploaded_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('storage_backend', sa.String(length=20), nullable=False),
        sa.Column('storage_bucket', sa.String(length=255), nullable=False),
        sa.Column('storage_key', sa.String(length=1024), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=False),
        sa.Column('file_size_bytes', sa.BigInteger(), nullable=False),
    )
    op.create_index(
        'ix_request_supporting_documents_request_id',
        'request_supporting_documents',
        ['request_id'],
    )

    # supporting_document_file_id previously pointed at dataset_files.id but
    # was never written to by any code path (confirmed via full-codebase
    # trace — see "Supporting Document Missing from Admin Review"
    # investigation), so it is always NULL on every existing row. Safe to
    # drop and recreate under the new name/target with no data migration.
    op.drop_column('dataset_requests', 'supporting_document_file_id')
    op.add_column(
        'dataset_requests',
        sa.Column(
            'supporting_document_id',
            UUID(as_uuid=True),
            sa.ForeignKey(
                'request_supporting_documents.id',
                ondelete='SET NULL',
                name='dataset_requests_supporting_document_id_fkey',
                use_alter=True,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('dataset_requests', 'supporting_document_id')
    op.add_column(
        'dataset_requests',
        sa.Column(
            'supporting_document_file_id',
            UUID(as_uuid=True),
            sa.ForeignKey('dataset_files.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.drop_index('ix_request_supporting_documents_request_id', table_name='request_supporting_documents')
    op.drop_table('request_supporting_documents')
