"""add storage_kind to dataset_files

Revision ID: 87560da9f24c
Revises: 3ec83a3f9ab2
Create Date: 2026-08-17

PLAN.md Phase 5 (Storage & Query Architecture) — persists how each
DatasetFile's actual data is stored/queried (StorageKind: row_records /
parquet / chunked_array / raster), replacing the old fragile
".zarr.zip"-suffix inference. Nullable: existing files have no reliable
provenance for this value from before the column existed and are never
fabricated as certain — see backfill_storage_kind.py for the separate,
explicit, best-effort backfill step.

Simple additive column + index — no FK, no data migration here.
"""

from alembic import op
import sqlalchemy as sa

revision = "87560da9f24c"
down_revision = "3ec83a3f9ab2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dataset_files", sa.Column("storage_kind", sa.String(length=20), nullable=True))
    op.create_index("ix_dataset_files_storage_kind", "dataset_files", ["storage_kind"])


def downgrade() -> None:
    op.drop_index("ix_dataset_files_storage_kind", table_name="dataset_files")
    op.drop_column("dataset_files", "storage_kind")
