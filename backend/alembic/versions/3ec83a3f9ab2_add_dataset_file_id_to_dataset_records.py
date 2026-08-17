"""add dataset_file_id to dataset_records

Revision ID: 3ec83a3f9ab2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-16

Adds DatasetRecord.dataset_file_id — provenance linking each row to the
DatasetFile whose ingestion wrote it. Lets a retry/cancellation cleanup
delete exactly "this file's rows" instead of relying on a transaction
rollback (which does not undo already-committed batches from a large
ingestion). Existing rows get dataset_file_id = NULL — their original
provenance cannot be reliably reconstructed and is not fabricated.

Safe against a live, large (800K+ row, growing) dataset_records table:
- ADD COLUMN ... NULL with no default is metadata-only, no table rewrite.
- The index is built CONCURRENTLY (autocommit_block, since CONCURRENTLY
  cannot run inside a transaction) — no blocking of concurrent reads/writes.
- The FK is added NOT VALID (near-instant, no data scan — NULL trivially
  satisfies any FK) then validated in a separate VALIDATE CONSTRAINT step
  (SHARE UPDATE EXCLUSIVE — scans existing rows but does not block
  concurrent reads/writes on dataset_records).
- No ON DELETE clause — defaults to NO ACTION. Deleting a DatasetFile
  while DatasetRecords still reference it must fail loudly, never
  silently cascade-delete millions of rows.
"""

from alembic import op
import sqlalchemy as sa

revision = "3ec83a3f9ab2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dataset_records", sa.Column("dataset_file_id", sa.UUID(), nullable=True))

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_dataset_records_dataset_file_id ON dataset_records (dataset_file_id)"
        )

    op.execute(
        "ALTER TABLE dataset_records "
        "ADD CONSTRAINT fk_dataset_records_dataset_file_id "
        "FOREIGN KEY (dataset_file_id) REFERENCES dataset_files(id) "
        "NOT VALID"
    )
    op.execute("ALTER TABLE dataset_records VALIDATE CONSTRAINT fk_dataset_records_dataset_file_id")


def downgrade() -> None:
    op.execute("ALTER TABLE dataset_records DROP CONSTRAINT fk_dataset_records_dataset_file_id")

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_dataset_records_dataset_file_id")

    op.drop_column("dataset_records", "dataset_file_id")
