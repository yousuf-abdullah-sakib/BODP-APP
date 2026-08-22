# Data Integrity — Phase 10.5

Two independent mechanisms, both real, both live-verified against the
dev stack: checksum-on-read (catches silent corruption of an individual
stored object at the moment it's re-read) and an orphan sweep (catches
DB/storage drift across the whole system — a row pointing at nothing, or
an object nothing points at).

## Checksum-on-read

`DatasetFile.checksum` is a SHA-256 hash captured once, at upload time
(`dataset_file_service.py`), against the raw bytes as originally
uploaded. Two places re-download that same raw object later and now
verify it against that checksum immediately after download, before doing
anything else with the bytes:

1. **Ingestion** (`app/worker/tasks/ingestion.py`, `_run_ingestion`) —
   right after the raw download loop, before `parser.parse(...)`. A
   mismatch raises a new `IntegrityError`
   (`app/services/ingestion_service.py`), caught by `process_dataset_file`
   in its own dedicated branch (distinct from `ParserError` — this is a
   storage-corruption signal, not a bad/malformed upload). The Upload row
   reaches `failed` with a clear message, and a system-authored
   `AuditLogEntry` (`actor_id=None`, `actor_name="System (Integrity
   Check)"`, `action_type="dataset"`, `target="dataset_file:<id>"`) is
   written so a real corruption incident is discoverable in the audit
   log, not just an ordinary ingestion failure indistinguishable from a
   bad file.

2. **Extraction** (`app/worker/tasks/extraction.py`, `_run_extraction`,
   non-Zarr branch only) — right after the source download loop, before
   `extractor.extract(...)`. A mismatch raises `ExtractorError` (the
   extraction pipeline's existing failure type), so the extraction fails
   cleanly through the code path that already exists for any other
   extraction failure.

   **Important scoping detail**: `_source_for_format()` resolves the
   source key to the RAW upload only for netcdf/mat-format extraction
   output. A csv/parquet-format extraction instead reads the PROCESSED
   Parquet artifact (a different object, with no checksum recorded
   against it at all) — verifying the raw checksum there would be a
   guaranteed false mismatch on every single extraction, not a real
   check. The wiring in `extraction.py` explicitly guards on
   `source_key == dataset_file.storage_key` before calling
   `verify_checksum`, so the check only ever runs when the object being
   read actually IS the checksummed one.

   The Zarr-materialization branch (`_materialize_zarr_to_parquet`, used
   when a gridded/Zarr-backed file is extracted to csv/parquet) is out of
   scope — a Zarr store's many chunk objects aren't individually
   checksummed today, and per-chunk verification would be a much larger
   change. Documented here as a known, deliberate gap, not silently
   expanded to cover.

`DatasetFile.checksum` is nullable (files predating checksum tracking) —
both call sites skip verification entirely when it's `None`, treating
"nothing to check against" as distinct from "checked and failed."

Automated regression coverage: `test_dataset_upload.py::TestChecksumOnRead`
(both the corruption-caught and the no-checksum-recorded-skips-cleanly
cases) and `test_extraction_phase5_zarr.py::TestChecksumOnRead`. Both
live-verified once more directly against the real dev stack before
landing: real file uploaded, bytes corrupted directly in MinIO, ingestion
re-run, confirmed `failed` status + audit log entry, confirmed cleanup.

## Orphan sweep

`app/worker/tasks/integrity.py` — checks both directions of DB/storage
drift, sharing one implementation between the nightly Celery task
(`integrity.orphan_sweep`, 4 AM UTC — after the 3 AM backup so that
night's fresh backup object is already a known key, before the 6 AM
admin-jobs window) and the manual CLI
(`python -m app.scripts.orphan_sweep`).

**DB-to-storage** (a DB row's key doesn't exist in storage — the more
actionable direction, since it means a real broken/404ing file some
granted user could hit): every non-null key across `DatasetFile` (raw
key AND the processed-artifact key/prefix recorded in its `file_metadata`
JSONB), `MediaFile`, `RequestSupportingDocument`, `SubsetExtraction`
(output only), `Report.output_storage_key`, `Backup.storage_key` is
checked via `storage.exists()`.

**Storage-to-DB** (an object exists with no DB row referencing it —
wasted disk, typically from a crashed task that uploaded before its DB
write committed): every configured backend (`vps_minio` always; `cloud`
only if its three `STORAGE_CLOUD_*` credentials are all set) is listed,
prefix by prefix, via the new `StorageService.list_keys()` method
(`app/services/storage/base.py`/`s3_backend.py` — a thin generator over
the same `list_objects_v2` paginator `delete_prefix()` already uses,
yielding one key at a time so an arbitrarily large prefix is never
loaded into memory at once). Each listed key is checked against a
known-key set built once per sweep run (not per-key-query) from the same
DB pass as the DB-to-storage direction.

`Report` and `Backup` have no bucket/backend columns at all — both are
hardcoded to `vps_minio` everywhere they're written/read in this
codebase (`worker/tasks/reports.py`, `worker/tasks/backups.py`), so the
sweep assumes the same. `Report`'s key isn't built by any `keys.py`
helper (it's assembled inline as `f"reports/{report.id}/{report.type}.csv"`)
— handled as a literal prefix (`"reports/"`), not a shared function
reference.

`snapshots/` and `previews/` are excluded from orphan findings
(counted for total-size visibility only, never flagged): a snapshot
object is deliberately overwritten in place on every regeneration
(`snapshot_key`'s own docstring), so an old snapshot object with no
current `Dataset.snapshot_version` match is expected, not a bug;
`previews/` has zero writers anywhere in current code.

**Report-only by design.** Neither the Celery task nor the shared
`run_sweep()` function ever deletes anything — a false positive here
(e.g. a key existing because a DB transaction hasn't committed yet,
racing the sweep) would be a real, if rare, data-loss risk if acted on
automatically. Findings land in the task's Celery result and as one
structured `integrity.orphan_found` warning log per finding. Only the
manual script (`orphan_sweep.py --delete <key> [<key> ...]`) can delete,
and only storage-to-db findings, one exact key at a time, checked against
the sweep's own just-computed findings before deleting — never a bulk
"delete everything found" action, and DB-to-storage findings (a broken
row) are never auto-deletable by this script either, since fixing those
needs a human decision about the affected row.

### Live-verified against the real dev stack (2026-08-22)

Ran `python -m app.scripts.orphan_sweep` against the actual dev-stack DB
and MinIO (not the isolated test DB) — this is real, current findings,
not a synthetic test:

- **28,844 storage-to-db orphans found** (`raw/`: 4,478, `processed/`:
  23,388, `extracts/`: 439, `avatars/`: 52, `supporting-documents/`: 3,
  `media/`: 215, `reports/`: 270) out of 30,793 objects listed — almost
  entirely leftover artifacts from this project's extensive live
  testing/benchmarking history (file timestamps span 2026-08-06 through
  2026-08-21; object sizes and naming, e.g. `viz_phase5_grid.nc`,
  `schema_review_test.csv`, match known test fixtures from this session,
  not real user data). None of the real, currently-referenced keys
  spot-checked (recent `Report` rows, real `DatasetFile` raw keys)
  appeared among the findings — no false positives found in spot-checks.
- **1 db-to-storage finding**: a `DatasetFile` row (`storage_key =
  "raw/fake"`, `uploaded_at` null) pointing at an object that was never
  real — a leftover manually-inserted test fixture row from earlier
  debugging this session, not a genuine broken user-facing file.
- Neither finding set was auto-deleted (by design). Both are flagged
  here for the project owner's own review/cleanup decision — this sweep
  deliberately does not decide that on its own.
- `--delete` flag verified against a deliberately-seeded throwaway
  object: found, listed, deleted, confirmed gone; a real, currently-
  referenced key was correctly refused (not a current finding for the
  given backend).

### Test matrix run

- `list_keys` pagination past 1000 keys (S3's own single-page cap):
  seeded 1,500 objects, confirmed the full set returned, no truncation —
  `test_storage.py::TestS3CompatibleBackend::test_list_keys_paginates_past_1000`.
- `list_keys` exact-set and empty-prefix correctness — same test class.
- Checksum-corruption caught during ingestion and extraction, both with
  automated regression tests and a direct live re-verification against
  the real dev stack (corrupt real MinIO bytes, re-run, confirm failure
  mode + audit log entry, confirm cleanup).
- Checksum skip-on-null-checksum (pre-tracking files) — automated test,
  confirms this is treated as "nothing to check," not a failure.
