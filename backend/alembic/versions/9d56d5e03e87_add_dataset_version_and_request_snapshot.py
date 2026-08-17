"""add dataset version and request coverage snapshot

Revision ID: a1b2c3d4e5f6
Revises: 87560da9f24c
Create Date: 2026-08-17

Fixes the Admin Data Requests page recomputing every request's coverage
(matching_record_count/dataset_total_record_count/matching_percent) from
scratch on every page load — confirmed via profiling to be ~96% of that
endpoint's server time. Adds:

- datasets.version: bumped by ingestion whenever record_count changes.
  Not nullable — every dataset (existing or new) starts at a real,
  correct value of 1, since "no ingestion has changed this dataset yet"
  is exactly what version=1 means for a dataset that predates this
  column too.
- dataset_requests.matching_record_count / dataset_total_record_count /
  matching_percent: the coverage snapshot, computed once at request
  creation (requests_service.create_request) instead of recomputed on
  every admin page load. Nullable — an existing request row has no
  snapshot to backfill from (recomputing it now would require the exact
  same expensive query this migration exists to stop doing routinely;
  a null snapshot is shown as "not available", never fabricated).
- dataset_requests.dataset_version: datasets.version at snapshot time,
  letting the admin dashboard flag a request's snapshot as stale
  (dataset.version has since moved on) without recomputing anything.
"""

from alembic import op
import sqlalchemy as sa

revision = "9d56d5e03e87"
down_revision = "87560da9f24c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("datasets", "version", server_default=None)

    op.add_column("dataset_requests", sa.Column("matching_record_count", sa.Integer(), nullable=True))
    op.add_column("dataset_requests", sa.Column("dataset_total_record_count", sa.Integer(), nullable=True))
    op.add_column("dataset_requests", sa.Column("matching_percent", sa.Float(), nullable=True))
    op.add_column("dataset_requests", sa.Column("dataset_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("dataset_requests", "dataset_version")
    op.drop_column("dataset_requests", "matching_percent")
    op.drop_column("dataset_requests", "dataset_total_record_count")
    op.drop_column("dataset_requests", "matching_record_count")
    op.drop_column("datasets", "version")
