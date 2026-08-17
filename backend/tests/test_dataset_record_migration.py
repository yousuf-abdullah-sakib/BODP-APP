"""Verifies the dataset_file_id migration's actual on-database result —
this suite runs against an already-migrated schema (conftest.py resets
via truncation, not per-test alembic upgrade/downgrade, matching this
repo's established testing pattern — see test_phase9_permission_migration.py).

The migration's upgrade()/downgrade() SQL itself (CREATE INDEX
CONCURRENTLY / ADD CONSTRAINT ... NOT VALID / VALIDATE CONSTRAINT / their
downgrade counterparts) was verified manually against the isolated test
DB during implementation: `alembic upgrade head`, `alembic downgrade -1`,
`alembic upgrade head` again, each completing without error and leaving
the expected schema state — not repeated here since this suite's fixture
model doesn't support DDL changes mid-run.
"""

import pytest
from sqlalchemy import text

from app.core.database import engine

pytestmark = pytest.mark.asyncio


class TestDatasetFileIdColumn:
    async def test_column_exists_and_is_nullable(self):
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'dataset_records' AND column_name = 'dataset_file_id'"
                )
            )
            row = result.fetchone()
        assert row is not None, "dataset_file_id column does not exist on dataset_records"
        assert row[0] == "YES", "dataset_file_id must be nullable — existing rows have no provenance"

    async def test_index_exists(self):
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'dataset_records' AND indexname = 'ix_dataset_records_dataset_file_id'"
                )
            )
            assert result.fetchone() is not None

    async def test_foreign_key_exists_validated_and_has_no_delete_cascade(self):
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT convalidated, confdeltype FROM pg_constraint "
                    "WHERE conname = 'fk_dataset_records_dataset_file_id'"
                )
            )
            row = result.fetchone()
        assert row is not None, "FK constraint fk_dataset_records_dataset_file_id does not exist"
        convalidated, confdeltype = row
        # asyncpg returns Postgres "char" columns as single-byte bytes.
        confdeltype = confdeltype.decode() if isinstance(confdeltype, (bytes, bytearray)) else confdeltype
        assert convalidated is True, "FK must be VALIDATEd, not left NOT VALID indefinitely"
        # confdeltype: 'a' = NO ACTION (the default, what this design
        # requires — approved explicitly to REJECT 'c' = CASCADE, which
        # would let a DatasetFile delete silently bulk-delete millions of
        # DatasetRecord rows with no explicit acknowledgement).
        assert confdeltype == "a", (
            f"Expected NO ACTION (confdeltype='a'), got {confdeltype!r} — "
            "ON DELETE CASCADE must never be used for this FK (see DatasetRecord."
            "dataset_file_id's model docstring for why)."
        )

    async def test_foreign_key_references_dataset_files(self):
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT confrelid::regclass::text
                    FROM pg_constraint
                    WHERE conname = 'fk_dataset_records_dataset_file_id'
                    """
                )
            )
            row = result.fetchone()
        assert row is not None
        assert row[0] == "dataset_files"
