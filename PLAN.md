# PLAN.md — Large-Scale Ingestion, Dynamic Schema & Visualization Roadmap

Status: **planning document only — no phase below has been implemented.**
This file supersedes the informal `B → A → C → D → E/G → F → H` sequencing
from earlier in the session. B and A are complete; everything after them
(previously C–H) is reorganized here into 5 dependency-ordered phases that
account for requirements discovered during and after Sub-phase A.

---

## 0. Where this fits against MASTER_PLAN.md

`MASTER_PLAN.md` Phases 1–9 (Foundation through Content/Analytics/Reporting)
are **built and in production use** — auth, catalog, requests/grants,
datasets, users/roles, visualization engine, subset extraction, admin
overview/reporting, and CMS/blog/media are all real, working features.
Nothing in this document touches or re-opens any of Phase 1–9's existing
scope; every phase below is explicitly scoped to extend the Phase 2/3/7
ingestion-catalog-visualize pipeline, not replace it.

This document's own two completed sub-phases (B, A) were discovered mid-
session while investigating a real production bug — two uploaded datasets
("Wave Data", "Model Wave Data") whose catalog filtering silently returned
no results despite genuine data existing.

---

## 1. Completed work (do not re-implement)

### Sub-phase B — Admin-configurable upload size, COMPLETE
- `backend/app/routers/admin_datasets.py`'s upload endpoint now reads
  `site_settings.max_upload_size_mb` via `settings_service.get_settings(db)`
  and enforces it, instead of silently falling back to the hardcoded
  `MAX_UPLOAD_SIZE_MB` env default.
- Test: `test_admin_configured_size_limit_is_enforced` (passing).

### Sub-phase A — DatasetRecord correctness, COMPLETE
- `backend/app/worker/tasks/ingestion.py`: new `_write_dataset_records()`,
  populates `DatasetRecord` from every tabular (CSV/NetCDF/.mat) upload;
  **explicitly excluded for GeoTIFF/raster** (verified: zero per-pixel
  rows on a 300×200 test raster).
- Parser fixes: `csv_parser.py`/`mat_parser.py`/`netcdf_parser.py` now all
  report `lat_col`/`lon_col`/`time_col` uniformly via `metadata.extra`;
  `netcdf_parser.py` gained `valid_time` as a recognized time-coordinate
  alias (ERA5/Copernicus convention — found live on Model Wave Data).
  `_write_dataset_records` restricts parameter rows to each parser's
  authoritative `metadata.variables` list (fixes a bug caught mid-session:
  auxiliary NetCDF coordinates like `number`/`expver` were briefly
  written as fake "variables" before this constraint was added).
- New: `backend/app/scripts/backfill_dataset_records.py`,
  `backend/app/scripts/cleanup_orphaned_upload.py`.
- **Live data fixed**: Model Wave Data now has 723,240 real `DatasetRecord`
  rows (`u10`/`v10`, 361,620 each); catalog filtering confirmed working
  end-to-end (`GET /catalog/{id}/records?parameter=u10` → 361,620 matches).
  Wave Data correctly has 0 records — its source CSV has no time column at
  all (a genuine data characteristic, not a bug); not fabricated.
  The abandoned `BoB_WaveData_2010_2024.nc` upload was cleaned up (Upload
  marked `failed`, orphaned `DatasetFile` row deleted, raw MinIO object
  deleted, verified no active Celery task) — never re-ingested, never
  treated as valid.
- Alembic head after A: **`f1a2b3c4d5e6`** (no new migration in A itself —
  A only changed application code + live data, not schema).
- Full backend suite after A: 414 passed, 1 pre-existing unrelated failure
  (`test_me.py::TestCmsBlocks::test_get_blocks_uses_cache_on_second_call`,
  confirmed failing in isolation, untouched by this work).

### Design decision made after A, approved, NOT yet implemented
`DatasetRecord.time`/`lat`/`lon` must become **nullable** — not every
dataset has every dimension (Wave Data has lat/lon but no time; other
real datasets may have time+lat/lon with no depth, or other shapes
entirely). This was investigated in depth (impact across
`catalog_service.py`, `visualize_service.py`, `worker/tasks/visualize.py`,
`admin_qc_service.py`, frontend types) and is folded into **Phase 2**
below, since ingestion cannot correctly detect "which dimensions exist"
without this schema change existing first.

---

## 2. What remains — full requirement inventory

The 24 numbered requirements from the current session are grouped here by
the dependency analysis in §3, not by the order they were listed:

| # | Requirement | Phase |
|---|---|---|
| 1 | Direct-to-MinIO presigned multipart upload | 1 |
| 2 | Separate upload-progress vs. processing-progress | 1 |
| 3 | Upload cancellation with full cleanup | 1 |
| 9 | Bulk import from server/NAS/existing storage | 2 |
| 4 | Chunked/RAM-safe processing, general | 2 |
| 5 | Dask + Xarray for NetCDF | 2 |
| 6 | Zarr for large multidimensional data | 2 |
| 7 | Windowed GeoTIFF processing + COG | 2 |
| 8 | Chunked HDF5 for MATLAB v7.3 | 2 |
| 16, 17 | Nullable DatasetRecord dimensions; no dimension assumed | 2 |
| 10 | Raw files always preserved in MinIO | 2 (verify/extend existing guarantee) |
| 11 | Postgres = metadata only, never raw data | 2 (already true; formalize for Zarr) |
| 12 | Automatic detection of ALL variables/columns, persisted at ingestion time | 2 |
| 13 | Dataset-specific schema registry (table + ingestion write path) | 2 |
| 14 | Admin variable/schema review UI | 3 |
| 15 | Assign variable roles (Dimension/Filter, Data, Visualization; multi-role) | 3 |
| 18, 21 | Dynamic dataset-specific filtering; hide filters with no data | 4 |
| 19, 20 | Dynamic dataset-specific visualization; use approved variables | 4 |
| 22, 23, 24 | Preserve Phase 1–9, small uploads, and the Wave Data / Model Wave Data fixes | **all phases, continuously** |

This yields **4 forward-looking phases**, not 5 — see §3 for why the
suggested 5-phase grouping collapses by one, and why the internal ordering
differs from the suggested names in two places.

---

## 3. Why this dependency order, not the suggested one

The prompt suggested:
`Phase 1 Upload → Phase 2 Processing → Phase 3 Cancellation+Bulk →
Phase 4 Schema Registry → Phase 5 Dynamic Filtering/Viz`

Two real dependency problems with that grouping, found by tracing what
each stage actually needs to exist first:

**(a) Cancellation cannot be its own later phase separate from upload
infrastructure.** Cancelling an upload means aborting a real MinIO
multipart session (`abort_multipart_upload`) that only exists once
presigned multipart upload (item 1) is built. Building multipart upload
in Phase 1 *without* designing its cancellation/abort semantics at the
same time means Phase 1's endpoints and `Upload`/`DatasetFile` schema
would need to be revisited and partially rewritten in a later phase —
exactly the mistake this document is supposed to avoid. **Cancellation
therefore belongs inside Phase 1, not Phase 3.**

**(b) Bulk import depends on Phase 2's ingestion rewrite, not Phase 1's
upload transport.** Bulk-importing a file already sitting on a NAS/server
skips the browser-upload step entirely — it needs a file to land in
MinIO's `raw/` prefix and then hand off directly to ingestion. That
hand-off point only makes sense once ingestion (Phase 2) is chunked/
RAM-safe, because bulk import is explicitly for 50GB–TB-scale files where
the old eager-loading parsers would OOM regardless of how the file
arrived in storage. **Bulk import therefore belongs in Phase 2**, sharing
its ingestion-triggering plumbing, not bundled with cancellation.

**(c) The schema registry's table must exist before Phase 2's ingestion
rewrite is finalized, even though the *admin review UI* on top of it
can come later.** Phase 2 needs `_write_dataset_records` (and its new
Dask/Zarr/windowed-GeoTIFF/chunked-.mat paths) to write **into** the
variable registry as part of detection — that's how "automatic detection
of ALL variables" (item 12) actually gets persisted, rather than being
detected and thrown away as it is today. If the registry table is
designed in a later phase, Phase 2's ingestion code would need to be
rewritten to backfill/write into a table that didn't exist when it was
built. **The registry's schema (table + write path) is therefore part of
Phase 2's database changes; the admin-facing review/approval workflow
on top of it is Phase 3.**

This collapses the suggested 5 phases into 4, because "Cancellation +
Bulk Import" doesn't survive as one coherent phase once traced against
real dependencies — its two halves belong in Phase 1 and Phase 2
respectively. The remaining structure:

```
Phase 1: Large Upload Infrastructure (incl. cancellation)
   │
   ▼
Phase 2: Large-File Processing, Automatic Variable Detection & Schema Registry
   │  (detection + persistence into the registry TABLE; not the admin UI)
   ▼
Phase 3: Admin Schema Review & Variable Role Assignment
   │  (review/approve/assign roles for what Phase 2 already detected)
   ▼
Phase 4: Dynamic Filtering & Visualization
   (consumes Phase 3's admin-approved roles)
```

Each phase is a hard prerequisite for the next — no phase writes code
that a later phase needs to tear out and redo.

---

## Phase 1 — Large Upload Infrastructure (with Cancellation)

### Objective
Replace the single synchronous "browser → FastAPI → MinIO" upload path's
size ceiling with genuine direct-to-MinIO presigned multipart upload,
scalable to TB-scale files, with real upload-progress reporting and a
fully-specified cancellation/cleanup lifecycle — while leaving the
existing small-file upload path working unchanged.

### Exact features included
1. Presigned multipart upload: initiate → per-part presigned PUT URLs →
   complete, entirely bypassing FastAPI for file bytes (control-plane
   JSON only touches the backend).
2. Real upload-progress tracking, driven by actual completed-part state
   (not a synthetic estimate) — `Upload.uploaded_bytes`/`total_size_bytes`.
3. Upload cancellation: abort the MinIO multipart session, delete any
   partial local temp state, mark `Upload` as a new terminal `CANCELLED`
   status, and guarantee no orphaned `DatasetFile`/`Dataset` shell row is
   left behind for an upload that never completed.
4. The existing single-request small-file upload endpoint is **not
   removed or altered in its external contract** — it continues to work
   exactly as today for files under the size threshold where multipart
   isn't warranted.

### Backend changes
- `backend/app/services/storage/base.py` — new abstract methods:
  `create_multipart_upload`, `presign_upload_part`,
  `complete_multipart_upload`, `abort_multipart_upload`, `list_parts`.
- `backend/app/services/storage/s3_backend.py` — boto3 implementations of
  the above (`create_multipart_upload`, `generate_presigned_url
  ("upload_part", ...)`, `complete_multipart_upload`,
  `abort_multipart_upload`).
- **New**: `backend/app/services/dataset_multipart_upload_service.py` —
  parallel to (not replacing) `dataset_file_service.py`. Owns
  initiate/presign-part/complete/cancel.
- `backend/app/routers/admin_datasets.py` — new endpoints:
  `POST /admin/datasets/{id}/uploads/initiate`,
  `POST /admin/datasets/uploads/{upload_id}/parts/{n}/presign`,
  `POST /admin/datasets/uploads/{upload_id}/complete`,
  `POST /admin/datasets/uploads/{upload_id}/cancel`.
- `backend/app/worker/tasks/ingestion.py` — `process_dataset_file` gains a
  cancellation checkpoint (checks `Upload.status == CANCELLED`) at each
  major step so a task already running when cancel is requested stops
  cleanly rather than completing anyway. (Full chunked-processing
  rewrite of this file is Phase 2 — Phase 1 only adds the checkpoint
  hooks, not the Dask/Zarr internals.)

### Frontend changes
- New/extended upload client (`frontend/src/lib/api/admin-datasets-
  multipart.ts` or extended `admin-datasets.ts`) implementing chunked
  browser-side upload (slice `File`, PUT each part to its presigned URL,
  track per-part completion for real progress).
- `AddDataToDatasetModal.tsx` (or its successor) — internal branch on
  file size: below threshold uses the existing single-request path
  unchanged; above threshold uses multipart. Real upload-progress bar
  driven by completed-part byte count. Cancel button wired to the real
  cancel endpoint (currently a dead stub that only closes the modal).

### Database/migration changes
New Alembic migration, chained onto `f1a2b3c4d5e6`:
- `uploads` table: add `multipart_upload_id` (str, nullable),
  `storage_key`/`storage_bucket` (reserved before the file exists),
  `total_size_bytes`, `total_parts`, `uploaded_bytes`, and extend
  `UploadStatus` with `INITIATED` (before parts land) and `CANCELLED`
  (terminal, distinct from `FAILED`).

### Storage/MinIO changes
No bucket/policy changes. New usage pattern only: real S3 multipart
upload lifecycle (`CreateMultipartUpload`/`UploadPart`/
`CompleteMultipartUpload`/`AbortMultipartUpload`) against the existing
`bodp-vps` bucket and existing `raw/{dataset_id}/{file_id}_{filename}`
key convention — no change to where files end up.

### Celery/Redis/worker changes
None structural in Phase 1 beyond the cancellation-checkpoint hooks noted
above. Queue separation and timeout coordination is Phase 2's concern
(it only matters once ingestion itself is long-running/chunked).

### Testing requirements
- `test_multipart_upload.py`: initiate/presign/complete happy path
  against a real MinIO test bucket (same pattern as existing
  `test_storage.py`).
- Cancellation tests (two scenarios, matching what was previously
  specified): (a) uploading → cancel → MinIO multipart aborted, no
  orphaned `DatasetFile`/`Dataset`, `Upload` shows `CANCELLED`; (b)
  upload completes → processing starts → cancel → ingestion task detects
  cancellation at its next checkpoint and stops, any partial processed
  artifact already written to MinIO is deleted, `DatasetRecord` rows
  written before the cancel are rolled back.
- Full existing `test_dataset_upload.py` suite must still pass unchanged
  — proves the small-file path is untouched.

### Rollback/recovery
Additive migration only (new nullable columns + new enum values) — safe
to roll back by dropping the new columns; no existing data is
transformed. New endpoints are additive; removing them (if a rollback is
needed) does not affect the existing single-request upload endpoint.

### What must NOT change in this phase
- The existing `POST /admin/datasets/{id}/files` single-request endpoint's
  request/response contract.
- `dataset_file_service.py`'s existing behavior for small files.
- Any parser (`csv_parser.py`/`netcdf_parser.py`/`mat_parser.py`/
  `geotiff_parser.py`) internals — Phase 1 does not touch parsing.
- `DatasetRecord` schema — the nullable-dimensions change is Phase 2.

### Acceptance criteria
- A file well over the current 5GB effective ceiling uploads successfully
  via the new multipart path in a live Docker test, with real progress
  reported throughout.
- Cancelling mid-upload and cancelling mid-processing both leave zero
  orphaned MinIO objects, zero orphaned DB rows, and a clear `CANCELLED`
  status — verified by direct MinIO/DB inspection, not just API response
  codes.
- Every pre-existing test in `test_dataset_upload.py`,
  `test_admin_datasets.py` still passes.
- A small-file upload through the admin UI still works exactly as before
  (manual verification).

---

## Phase 2 — Large-File Processing, Automatic Variable Detection & Schema Registry

### Objective
Make the ingestion pipeline actually safe for the files Phase 1 can now
receive — chunked/RAM-safe NetCDF (Dask+Xarray), windowed GeoTIFF (COG),
chunked HDF5 `.mat` — and, in the same pass, make **automatic variable/
dimension detection a persisted outcome of ingestion, not a discarded
byproduct**: every variable/column/dimension a parser detects is written
into a new **schema registry table** (`DatasetVariable`). This phase owns
detection and persistence only — it does **not** include any admin-facing
review, approval, or role-assignment UI; a `DatasetVariable` row created
here has no reviewed/approved role yet. That human-in-the-loop workflow
is entirely Phase 3's responsibility, built on top of what this phase
persists. `DatasetRecord`'s dimensions also become nullable in this
phase, since ingestion cannot correctly detect "which dimensions a file
actually has" while the schema still forces `time`/`lat`/`lon` to always
be present.

### Exact features included
- **NetCDF**: `to_processed()` rewritten to use Dask-backed xarray
  (`chunks=` on `xr.open_dataset`), replacing the current
  `ds.to_dataframe()` full-materialization call. Chunk-axis choice
  (time-based, per your stated preference for oceanographic/model data)
  determined by inspecting the actual dataset's real dimensions at parse
  time, not a hardcoded constant.
- **Zarr**: introduced as the processed-artifact format specifically for
  large multidimensional NetCDF where a chunked/indexable array
  representation is more appropriate than a flattened tidy Parquet table
  — chosen by a size/dimensionality heuristic in `to_processed()`, with
  Parquet remaining correct for genuinely tabular data (CSV, and modest
  NetCDF that fits the existing tidy-table shape). New storage-key
  convention needed (a Zarr store is a directory of many chunk objects,
  not one blob — `storage/keys.py`'s existing docstring already scoped
  this: either a `raw_prefix()`-style multi-object key, or a
  `.zarr.zip` container as the simpler bridge).
- **GeoTIFF**: `to_processed()` rewritten to windowed/blockwise
  read-and-write (`rasterio`'s `block_windows()`), replacing the current
  `src.read()` full-array load. COG output format unchanged (already
  correct) — only the internal read/write pattern changes.
- **MATLAB**: v7.3 (`h5py`-backed) `to_processed()` rewritten to sliced
  reads instead of `item[()]` full materialization. Legacy (pre-v7.3,
  `scipy.io.loadmat`) has no chunked-read API in the library itself —
  files above a size threshold are rejected with a clear, actionable
  error message (matches the earlier-agreed decision) rather than
  attempting a load that will OOM.
- **DatasetRecord nullable dimensions**: `time`/`lat`/`lon` become
  `Mapped[... | None]` (mirrors the existing `depth_m`/
  `Dataset.temporal_start` nullable pattern already in the codebase).
  `_write_dataset_records` detects which dimensions actually exist per
  file and writes only those, instead of skipping the whole file when
  one is missing (this is what finally makes files like Wave Data's
  timeless CSV populate real records under a future re-run, without
  fabricating a fake date). A new validation guard prevents a fully
  "dimension-less" row (must have at least one of: time, (lat & lon),
  station_id).
- **Query-layer null-safety fixes**, confirmed necessary by prior
  investigation: `catalog_service.py`'s preview `ORDER BY time DESC`
  gets an explicit `NULLS LAST`; `admin_qc_service.py`'s duplicate-
  detection grouping is adjusted so timeless rows of the same parameter
  don't collapse into one false-positive duplicate bucket.
- **Automatic variable detection, persisted via a new schema registry
  table** (new `DatasetVariable` model): every variable/dimension a
  parser detects is now written to the database as a real row, instead
  of being detected and discarded as it is today — `dataset_id`, `name`,
  `data_type`, `unit`, `role` (nullable at this stage — Phase 3 is what
  lets an admin actually set it), `is_dimension`, `min_value`/
  `max_value` (numeric range, for later filter UI), a capped/sampled
  `distinct_values` for categorical fields (never an unbounded live
  `SELECT DISTINCT` at TB-scale row counts). Populated by ingestion at
  detection time, for every format (CSV/NetCDF/.mat/GeoTIFF). **No
  admin-facing review/approval surface exists yet — that is entirely
  Phase 3.** This phase's job ends at "detected and stored," not
  "reviewed and usable."
- **Bulk import**: new admin-triggered flow for a file already present on
  a mounted path/NAS — copies it into MinIO under the standard `raw/`
  key convention (reusing existing `storage.put`/multipart-put logic
  from Phase 1 for anything large), creates the same `Upload`/
  `DatasetFile` shell rows a normal upload would, and dispatches the
  exact same `process_dataset_file` ingestion task — no parallel
  ingestion code path, just a different entry point into the same one.
- **Celery queue separation & timeouts**: dedicated `ingestion` queue,
  `concurrency=1` initially (per earlier agreement, to avoid concurrent
  large jobs compounding memory pressure), `task_soft_time_limit`
  computed from file size rather than one global constant. Coordinated
  Nginx (`proxy_read_timeout`/`proxy_send_timeout`,
  `proxy_request_buffering off`) and Gunicorn (`--timeout`) values —
  made configurable via `app/core/config.py` settings rather than
  hardcoded, with defaults documented as assumptions pending real
  deployment network characteristics.

### Backend changes
- `backend/app/services/parsers/netcdf_parser.py`,
  `geotiff_parser.py`, `mat_parser.py` — `to_processed()` rewrites as
  above. `FileParser`/`ParsedFileMetadata`/`ProcessedArtifact` interface
  in `base.py` gains whatever minimal fields are needed to describe a
  Zarr artifact (e.g. `file_extension="zarr.zip"` or a directory-key
  variant) without breaking the existing Parquet/COG return shape for
  other formats.
- `backend/app/worker/tasks/ingestion.py` — `_write_dataset_records`
  updated for nullable dimensions; new `_write_variable_registry` (or
  folded into the same function) populating `DatasetVariable`.
- `backend/app/models/catalog.py` — `DatasetRecord` nullable columns;
  new `DatasetVariable` model.
- `backend/app/worker/celery_app.py` — `task_routes`, per-task soft time
  limit.
- `backend/app/core/config.py` — new configurable timeout settings.
- **New**: `backend/app/scripts/bulk_import.py` or an admin router
  endpoint (decision point: CLI script for server-side NAS access vs.
  an admin-panel-triggered path — depends on deployment topology, flagged
  for confirmation before implementation).
- `backend/app/services/admin_qc_service.py` — duplicate-detection
  grouping fix for nullable `time`.
- `backend/app/services/catalog_service.py` — `NULLS LAST` ordering fix.

### Frontend changes
- None required for filtering/visualization yet (that's Phase 4) — only
  whatever minimal admin UI is needed to trigger bulk import (a new
  small form/button, following existing admin-section conventions) and
  to reflect the new `Upload` states if bulk import surfaces its own
  progress.
- `frontend/src/lib/types/catalog.ts` — `DatasetRecordPreview.time`
  becomes `string | null` (type-level fix matching the backend's now-
  nullable field).

### Database/migration changes
Two migrations, chained in order onto Phase 1's final head:
1. `DatasetRecord.time`/`lat`/`lon` → nullable (additive-safe, confirmed
   low-risk — loosening a constraint never invalidates existing rows;
   the existing `time` btree index behaves correctly with nulls).
2. New `dataset_variables` table (the schema registry).

### Storage/MinIO changes
New key convention for Zarr artifacts under `processed/` (multi-object
or `.zarr.zip`, per the design decision above) — `raw/` convention
unchanged. Bulk import writes under the same `raw/` prefix as any other
upload; **no separate storage path for bulk-imported files** — this is
what satisfies requirement 10 (raw files always preserved in MinIO)
uniformly regardless of how they arrived.

### Celery/Redis/worker changes
New dedicated `celery-worker-ingestion` service in both `docker-
compose.yml` and `docker-compose.prod.yml`, `-Q ingestion
--concurrency=1`; existing `celery-worker` restricted to its current
queues via `-Q` so lightweight tasks (notifications, extraction, etc.)
are never blocked behind a large ingestion job.

### Testing requirements
- Real (not tiny-fixture) large-file tests for NetCDF/GeoTIFF/.mat,
  including **memory-ceiling tests** that run the parser under an
  artificially constrained memory limit and assert successful completion
  — this is the only way to prove chunking is genuinely happening rather
  than merely producing correct output on a small-enough file that would
  pass even with the old eager-loading code. Gated behind a slow-test
  marker per `docs/TESTING.md` conventions, not run in the default fast
  loop.
- `test_dataset_upload.py`/`test_parsers.py` existing correctness tests
  continue to pass unchanged on small fixtures.
- New tests: nullable-dimension `DatasetRecord` writes (time-only, lat/
  lon-only, and all-present cases), the dimension-less-row rejection
  guard, `DatasetVariable` population per format, bulk import happy path.
- Legacy `.mat` size-threshold rejection test (clear error, not a hang/
  OOM attempt).

### Rollback/recovery
Both migrations are additive/nullable-relaxing — safe to roll back.
Parser rewrites are the highest-risk item in this phase: each format's
old and new `to_processed()` behavior must be verified to produce
equivalent output on the existing small test fixtures before being
trusted on large real files, so a regression is caught by existing tests
before it reaches production data.

### What must NOT change in this phase
- Phase 1's upload/multipart/cancellation endpoints and contracts.
- Any admin-facing UI for reviewing variables — that's explicitly Phase 3,
  not built here even though the table it needs exists after this phase.
- `catalog_service.py`'s filter *shape* (`RecordsFilter`'s fixed fields)
  — still the same hardcoded filter set at the end of this phase; only
  its null-handling changes. Dynamic, schema-driven filtering is Phase 4.
- The already-fixed Wave Data / Model Wave Data live data from Sub-phase
  A — this phase's nullable-dimension change makes it *possible* to
  re-backfill Wave Data with real (still time-less) records in a future
  pass, but does not itself touch that live data without a separate,
  explicit confirmation step (matching how Sub-phase A always asked
  before writing to live data).

### Acceptance criteria
- A multi-GB real NetCDF/GeoTIFF file processes successfully through
  ingestion without exceeding a deliberately constrained memory limit in
  a test environment.
- `DatasetVariable` rows exist and correctly reflect real
  variables/dimensions after ingesting each of the 4 supported formats.
- Bulk-importing a file from a mounted path produces an identical
  `Dataset`/`DatasetFile`/`DatasetRecord`/`DatasetVariable` result to
  uploading the same file through the API.
- All pre-existing tests still pass; new memory-ceiling tests pass.

---

## Phase 3 — Admin Schema Review & Variable Role Assignment

### Objective
This phase adds **no new detection logic** — automatic detection and
persistence of variables/dimensions is entirely Phase 2's job and is
already done by the time this phase starts. Phase 3's job is purely the
**human-in-the-loop review layer on top of** what Phase 2 already
persisted: give admins a real screen to see every `DatasetVariable` row
Phase 2's ingestion wrote for each dataset, and to assign each one's role
(Dimension/Filter, Data Variable, Visualization Variable — multiple roles
where appropriate). A dataset's dynamic filtering/visualization behavior
(Phase 4) is gated on this admin approval, not on raw auto-detection
alone — an unreviewed `DatasetVariable` has no role and is not yet usable
for filtering or visualization.

### Exact features included
- New admin permission (`"Review Datasets"` or similar — a new
  `PERMISSION_LIST` entry, since no existing permission naturally covers
  this without conflating it with routine dataset editing), seeded via
  the same data-migration pattern as the existing Phase 9 permission
  seed (`alembic/versions/a3c47b895dae_seed_phase9_permissions.py`).
- New admin review queue UI, following the existing, proven Dataset
  Requests approve/reject pattern (`AdminRequestsSection.tsx`/
  `requests_service.py`) rather than inventing new UI conventions:
  list of datasets with pending/unreviewed variables, per-variable role
  assignment controls, approve action.
- `DatasetVariable.role` (added as a real, non-null-after-review field
  in this phase, having been nullable/unset since Phase 2) plus a new
  per-dataset "schema reviewed/approved" gate — implemented as an
  **orthogonal state**, not a new `UploadStatus` value, so Phase 1's
  existing upload-status polling logic (`AddDataToDatasetModal.tsx`,
  which only branches on `complete`/`failed`) is never touched by this
  phase. A dataset can be fully ingested (`Upload.status = complete`)
  and still be "pending schema review" — these are deliberately separate
  concerns.
- Admin can assign multiple roles to one variable (e.g. a variable that's
  both a Data Variable and Visualization Variable) — modeled as
  `DatasetVariable.roles: ARRAY(String)` or a join table, decided during
  implementation based on whether role combinations need independent
  querying (array is simpler and matches the existing `Dataset.
  platforms`/`parameters` array-column convention already used
  throughout this codebase).

### Backend changes
- `backend/app/models/user.py` — `PERMISSION_LIST` +1.
- New `backend/app/schemas/admin_dataset_schema.py`,
  `backend/app/services/admin_dataset_schema_service.py`,
  `backend/app/routers/admin_dataset_schema.py` (or extend
  `admin_datasets.py` — decided during implementation based on router
  size) — list pending-review datasets, get/update a dataset's variable
  roles, mark reviewed.
- `backend/app/models/catalog.py` — `DatasetVariable.role`(s) field
  finalized; new `Dataset.schema_reviewed_at`/`reviewed_by` (or a
  separate lightweight review-state table, mirroring `QualityIssue`'s
  existing structure) for the orthogonal approval gate.

### Frontend changes
- New admin section (nav entry + component), modeled directly on
  `AdminRequestsSection.tsx`'s queue/detail pattern: `AdminDatasetSchema
  ReviewSection.tsx` (or similar) — per-dataset variable list, role
  checkboxes/selectors, approve button.
- `AdminDatasetsSection.tsx`/dataset detail view — surface `file_metadata`
  (currently fetched but never rendered anywhere, confirmed this
  session) and a "schema review status" badge, giving admins visibility
  into what's pending without a separate deep-dive.

### Database/migration changes
One migration: `DatasetVariable.role`/`roles` column, plus the review-
state tracking (new column(s) on `Dataset` or a new lightweight table —
decided during implementation, both are additive/low-risk).

### Storage/MinIO changes
None.

### Celery/Redis/worker changes
None — this phase is pure CRUD/admin-workflow, no new background jobs.

### Testing requirements
- Permission enforcement test (a token without the new permission gets a
  real 403).
- Review workflow test: seed detected-but-unreviewed variables, assign
  roles via the new endpoint, assert persistence and the approval-gate
  state flips correctly.
- Multi-role assignment test (one variable holding 2+ roles
  simultaneously).
- Regression: existing dataset CRUD (`AdminDatasetsSection.tsx`,
  `admin_datasets_service.py`) unaffected.

### Rollback/recovery
Purely additive schema + new endpoints/UI — safe to roll back without
touching any Phase 1/2 data. Datasets ingested before this phase simply
show as "not yet reviewed" until an admin visits the new screen; nothing
forces a backfill of review state.

### What must NOT change in this phase
- Phase 2's ingestion/detection logic itself — this phase only adds a
  human review layer on top of data Phase 2 already writes.
- The catalog/Visualize filtering behavior — still using Phase 2's
  fixed/global shape until Phase 4. A dataset being "reviewed" in this
  phase has no visible effect on the public site yet; that wiring is
  Phase 4's job.

### Acceptance criteria
- An admin with the new permission can see every real dataset's detected
  variables (including Wave Data's and Model Wave Data's) and assign
  roles to each.
- An admin without the permission is correctly blocked (403).
- The review state persists correctly and is queryable per-dataset.

---

## Phase 4 — Dynamic Filtering & Visualization

### Objective
Make the catalog's filter UI and the Visualize module both genuinely
dataset-driven, consuming Phase 3's admin-approved variable roles instead
of today's global taxonomy (Visualize) and fixed hardcoded field set
(catalog filters) — completing the full diagram: Upload → Detection →
Review → Schema → Dynamic Filtering → Dynamic Visualization.

### Exact features included
- Catalog dataset-detail filter UI becomes schema-driven: for a selected
  dataset, only variables/dimensions with an approved Dimension/Filter
  role render a filter control at all, and the control type is chosen by
  the variable's detected shape — date range for a time dimension,
  spatial bounds/map selection for lat/lon, numeric min/max for a
  continuous data variable, dropdown/multi-select for a categorical one
  (sourced from `DatasetVariable.distinct_values`, capped/sampled — never
  a live unbounded `SELECT DISTINCT` over potentially billions of rows).
  A dataset lacking a given dimension (e.g. Wave Data's missing time)
  simply never renders that filter — satisfying the explicit requirement
  that "if a dataset does not contain a field, its filter must not
  appear."
- Visualize's parameter/variable selection becomes dataset-scoped:
  replaces the current global `/catalog/taxonomy`-sourced dropdown with
  a per-dataset list of variables carrying the approved Visualization
  Variable role. Backend `visualize_service.py`'s currently-unvalidated
  `parameter: str` fields gain real validation against the selected
  dataset's approved variable set (today any string is silently accepted
  or silently returns empty — this closes that gap).
- Catalog list-page filters (category/parameter/source/platform/format
  dropdowns, currently sourced from a flat cross-dataset taxonomy) are
  explicitly **out of scope for this phase** unless later confirmed
  otherwise — those are deliberately cross-dataset discovery filters, a
  different concern from a single dataset's detail-page filters; folding
  them into the same per-dataset schema-driven model is a separate
  design question flagged here, not decided.

### Backend changes
- `backend/app/services/catalog_service.py` — `RecordsFilter`/
  `_apply_record_filters` restructured to build its WHERE clause from a
  dataset's actual approved `DatasetVariable` rows rather than a fixed
  Python dataclass with one field per possible filter.
- New endpoint: `GET /catalog/{id}/schema` (or similar) — returns the
  dataset's approved, filterable variable definitions for the frontend
  to render filters from.
- `backend/app/services/visualize_service.py` (622 lines, the largest
  single file touched in this whole roadmap) — every `parameter`
  lookup (6+ call sites identified this session) gains validation against
  the dataset's approved Visualization Variable set.
- `backend/app/schemas/visualize.py` — `parameter: str` fields gain
  real validation (currently only `min_length=1`).

### Frontend changes
- `frontend/src/app/catalog/[id]/useDatasetFilters.ts` (142 lines) and
  `DatasetDetailClient.tsx` (454 lines) — substantial rework from a
  fixed field list to dynamic rendering driven by the new schema
  endpoint.
- `frontend/src/app/visualize/VisualizeClient.tsx`,
  `useVizFilters.ts` — parameter dropdown sourced from the selected
  dataset's approved variables instead of global taxonomy; the current
  hardcoded default (`parameter: "Sea Surface Temp"`) removed.
- `frontend/src/lib/types/catalog.ts`, `frontend/src/lib/types/
  visualize.ts` — new types for the schema-driven filter/variable shape.

### Database/migration changes
None anticipated beyond what Phases 2–3 already introduced — this phase
is primarily a read/query-shape change, not a schema change. (If the
categorical `distinct_values` sampling approach needs its own supporting
index or materialized view for performance at scale, that would be
decided and added during implementation, not pre-specified here.)

### Storage/MinIO changes
None.

### Celery/Redis/worker changes
None anticipated, though categorical-filter distinct-value computation
at genuinely large scale may warrant a background-computed/cached
approach rather than a synchronous query — flagged as an implementation-
time decision, not pre-committed here.

### Testing requirements
- Schema-driven filter rendering test: a dataset with only lat/lon (no
  time, no depth) renders exactly those filters and no others.
- Filter correctness test: filtering by an approved Dimension/Filter-role
  variable produces correct results; a variable without that role cannot
  be filtered on even if present in `DatasetRecord`.
- Visualize parameter validation test: an unapproved/nonexistent
  parameter is rejected with a clear error instead of silently returning
  empty data.
- Full regression pass on existing catalog/Visualize tests.
- Manual verification in a browser: Wave Data's detail page shows no
  date filter; Model Wave Data's shows a working date filter and its
  Visualize integration lists only `u10`/`v10` (assuming those are
  approved as Visualization Variables in Phase 3).

### Rollback/recovery
This is the highest-blast-radius phase for user-facing behavior (it
changes what real users see on the catalog and Visualize pages) — recommend
a feature-flag or gradual rollout (e.g. schema-driven filtering active
only for datasets with an approved schema, falling back to the current
fixed-filter behavior for anything not yet reviewed) rather than a hard
cutover, so Phase 3's review backlog doesn't have to be fully cleared
before this phase can ship.

### What must NOT change in this phase
- The underlying `DatasetRecord`/`DatasetVariable` schema — this phase
  only changes how they're queried and rendered, not their structure.
- Subset extraction / download (`extraction_service.py`) — confirmed
  this session to operate on raw source files directly, never
  `DatasetRecord`, so it is structurally unaffected by any part of this
  roadmap.
- Catalog list-page cross-dataset discovery filters (explicitly deferred,
  see above).

### Acceptance criteria
- Every requirement in §2's table mapped to Phase 4 is demonstrably true
  in a live browser check: dynamic per-dataset filtering, dynamic
  per-dataset visualization, visualization using approved variables only,
  filters absent for missing dimensions.
- No regression in any dataset that predates this phase's rollout
  (verified via the fallback/flag strategy above).

---

## Phase 5 — Storage & Query Architecture (No New Data Rows in PostgreSQL)

**Status: RESCOPED.** An earlier, narrower version of this phase (hybrid:
gridded data → Zarr, tabular data stays on `DatasetRecord`) was written,
then explicitly superseded after further discussion. This version
replaces it entirely — PostgreSQL stops being a data store for *any*
newly-ingested observation values, tabular or gridded.

### Objective
The real target, stated directly: **PostgreSQL should manage the data,
but MinIO should store the data.** Every uploaded file's actual
observation values — regardless of format — live in MinIO as a
columnar/chunked artifact (Parquet for tabular, Zarr for gridded, COG
for raster, all already true today for raster and partly true for
gridded). PostgreSQL holds only what's genuinely relational: dataset
metadata, `DatasetVariable` schema, permissions, requests/grants,
catalog listings, audit — never a row-per-observation table again for
new data. Filtering and visualization query the MinIO-resident files
directly through a real columnar query engine (DuckDB for Parquet,
`xarray`/Zarr for gridded) with predicate pushdown, not by loading a
file and filtering it in Python, and not by pre-flattening it into SQL
rows.

This supersedes the narrower Phase 5 draft's premise that "sparse/
tabular data is correctly served by `DatasetRecord` today" — it is
*functionally* correct today, but it doesn't belong in the target
architecture either: `DatasetRecord`'s existence at all is what let a
5MB gridded file's shape mismatch go unnoticed as "just a scale
problem," and keeping large tabular ingestion on the same row-per-
observation model preserves the identical failure mode for a large CSV
(a 12M-row station-observation file is just as real a possibility as a
12M-element grid). Removing the row-per-observation path for *all* new
ingestion removes the size judgment call entirely, for every format.

### Explicit correction from the narrower draft, and why
The narrower Phase 5 kept `DatasetRecord`+the COPY-based bulk writer
(`_write_dataset_records`, `_COPY_CHUNK_ROWS`, benchmarked at ~12,000–
13,700 rows/sec) as "the correct mechanism" for tabular data. That
framing is dropped here: the COPY writer is fast at what it does, but
what it does — turn every observation into an indexed SQL row — is
exactly the model this rescoped plan moves away from, for tabular data
too. The writer and its whole test/benchmark suite become **legacy-path
support only** (kept working for existing data, not extended or reused
for new ingestion) — see "What happens to the existing COPY writer"
below.

### Guiding decision — verified, not just planned
**DuckDB reading Parquet directly from MinIO, with genuine predicate
pushdown, was empirically tested against this project's real dev-stack
MinIO before this plan was written** (not merely reasoned about from
documentation):
- A 1.875MB / 100,000-row Parquet file was uploaded to the real
  `bodp-vps` MinIO bucket via the same path-style S3 addressing the
  existing `S3CompatibleBackend` already uses.
- DuckDB (`httpfs` extension), configured with the exact same four
  config values `storage/registry.py` already resolves (endpoint,
  access key, secret key, region) plus `s3_url_style='path'`, queried it
  successfully: `SELECT COUNT(*) ... WHERE parameter='Salinity'` returned
  in ~31ms; a `GROUP BY` aggregate in ~37ms.
- `EXPLAIN ANALYZE` on the filtered query showed **`in: 40.9 KiB`
  transferred, 2 GET requests total, `Filters: parameter='Salinity'`
  pushed directly into the Parquet `TABLE_SCAN`** — i.e., roughly 2% of
  the file's bytes were fetched to answer the query, confirming real
  HTTP-range-based row-group pruning against MinIO, not a full-file
  download.
- A 3-file glob (`read_parquet('s3://bucket/prefix/*.parquet')`)
  correctly unioned all three files into one 3,000-row result — the
  mechanism multi-`DatasetFile` datasets need (`Dataset`→`DatasetFile`
  is one-to-many, confirmed in `catalog.py:93-111`).
- DuckDB's `spatial` extension's `ST_Intersects(ST_Point(...),
  ST_MakeEnvelope(...))` was confirmed callable and returns correct
  results — a direct, near-1:1 replacement for `catalog_service.py:356`/
  `visualize_service.py:97`'s existing `func.ST_Intersects(DatasetRecord.
  geom, envelope)` PostGIS calls, preserving the polygon-AOI-readiness
  the current code's own comment explains was the deliberate reason
  `ST_Intersects` was chosen over a plain BETWEEN range filter.

**What was explicitly NOT verified, and must be benchmarked before any
concurrency claim is made** — flagged per direct feedback that
"well-trodden" is not the same as a production guarantee:
- DuckDB's actual behavior under 400–500 *concurrent* connections/queries
  against this deployment's real CPU/RAM/network — connection/process
  model, sustainable query rate, and whether concurrent large scans
  compete for the same MinIO bandwidth in a way that degrades latency.
- Real-world row-group pruning effectiveness depends on how each Parquet
  file is written (row-group size, whether relevant columns carry
  min/max stats) — the empirical test above used pyarrow's default
  writer settings; production ingestion's actual writer configuration
  must be checked to produce Parquet files that get the same pruning
  benefit, not assumed automatically true for every file.
- MinIO's own throughput ceiling under many simultaneous GET/HEAD
  requests from many concurrent DuckDB queries.
- Whether a connection-pooled/cached DuckDB process model (e.g. one
  long-lived DuckDB connection per worker, reused across requests) or a
  fresh connection per request is the right operational shape — this is
  an implementation decision requiring its own benchmark, not assumed.

**This phase's testing requirements (below) include a dedicated
concurrency benchmark section specifically because of this — no claim
about safe concurrent user counts ships without a number behind it.**

### What happens to existing `DatasetRecord` data — explicit, non-destructive
**Existing `DatasetRecord` rows are never deleted, migrated, or
touched by this phase.** The two architectures run in parallel:
- Every `DatasetFile` ingested **before** this phase's rollout keeps
  being served by the existing `DatasetRecord`/`catalog_service.py`/
  `visualize_service.py` SQL-query path, completely unchanged — an old
  dataset's filter/Visualize behavior does not change at all.
- Every `DatasetFile` ingested **after** rollout uses the new Parquet/
  Zarr/DuckDB/`xarray` path exclusively — it never gets a
  `DatasetRecord` row written for it in the first place.
- A dataset that later gets a *second* file uploaded post-rollout could
  therefore have some files on the old path and some on the new path
  simultaneously — `catalog_service.py`'s query functions must combine
  both sources' results for such a dataset (union the SQL-sourced rows
  and the DuckDB-sourced rows), not silently drop one. This mixed-state
  case is explicitly called out as a required test scenario, not an
  edge case to discover later.
- A future, separate, explicitly-approved phase can decide whether/how
  to retire `DatasetRecord` for old data (e.g. backfilling old raw files
  into Parquet and dropping the SQL rows) — **not decided or scheduled
  here.** This phase's job is only to stop the bleeding for new
  ingestion and prove the new path works; retiring old data is a
  distinct decision with its own risk profile (verifying byte-identical
  re-derivation of old data, downtime/consistency during cutover, etc.)
  that deserves its own dedicated plan when it's actually proposed.

### Exact features included

**1. Parsers write Parquet (tabular) or Zarr (gridded) to `processed/`
— same artifacts already produced today, but they become the primary
data, not an intermediate step toward `DatasetRecord`.**
`csv_parser.py`/`netcdf_parser.py`/`mat_parser.py` already write these
formats in `to_processed()` — this phase's parser-level change is
narrow: `netcdf_parser.py`/`mat_parser.py`'s existing 50,000,000-element
Zarr-vs-Parquet size threshold is replaced by a shape check (any
confirmed regular grid → Zarr, always, regardless of size — the same
"shape not size" correction already agreed for gridded data specifically
applies here as the general rule for every format). GeoTIFF's COG
output is unchanged (already correct, already the only format never
touching `DatasetRecord`).

**2. Zarr's storage layout changes from one zipped object to
one-object-per-chunk, exactly as previously planned** — `StorageService.
get()` has no `Range` support, so a single `processed.zarr.zip` object
requires a full download to read at all, defeating chunked storage's
purpose. New layout: `processed/{dataset_id}/{file_id}.zarr/...` (many
small chunk + metadata objects under a shared prefix, matching `zarr`'s
own native directory-store convention and the standard `fsspec`/`s3fs`-
on-S3 pattern), read via `xr.open_zarr` through `fsspec`'s S3 support
talking to MinIO directly with the same credentials `registry.py`
already resolves.

**3. No new `DatasetRecord` rows are ever written by new ingestion, for
any format.** `ingestion.py`'s `_write_dataset_records` call is removed
from `_run_ingestion`'s normal flow entirely (not conditionally skipped
— structurally absent from the new-ingestion path). The function itself,
its COPY writer, and its whole test suite remain in the codebase,
untouched, because they must keep working for whatever administrative
action might still reference them (e.g. `backfill_dataset_records.py`
remains valid for repairing pre-existing data) — but nothing in the live
upload/bulk-import/ingestion flow calls it anymore.

**4. `DatasetFile.storage_kind` becomes the persisted routing fact**
(`row_records` | `parquet` | `chunked_array` | `raster`) — `row_records`
is the value every pre-Phase-5 file effectively has (backfilled via a
one-time best-effort script from existing `file_metadata`, matching the
provenance-discipline already established this session — never
fabricated as certain), `parquet`/`chunked_array`/`raster` are what new
ingestion sets going forward. This is the field the query router (item
5) branches on.

**5. `catalog_service.py`/`visualize_service.py` gain a routing layer
in front of their existing query functions, not a rewrite of them.**
New `backend/app/services/tabular_query_service.py` (DuckDB-backed,
Parquet) and (from the earlier draft, unchanged in shape)
`gridded_query_service.py` (`xarray`/Zarr-backed) — each dataset's
`DatasetFile` rows are grouped by `storage_kind`; `row_records` files
query `DatasetRecord` exactly as today (existing code, unmodified);
`parquet` files query via `tabular_query_service`; `chunked_array` files
query via `gridded_query_service`; results from however many groups a
given dataset actually has are combined into one response matching
today's existing `get_filtered_records`/Visualize response shapes
exactly, so no frontend contract changes. This is genuinely additive —
every line of `_apply_record_filters`/`RecordsFilter`/the five Visualize
query functions keeps working unmodified for `row_records`-backed data.

**6. `tabular_query_service.py`'s DuckDB layer translates
`RecordsFilter`/`VizFilterParams` into SQL against `read_parquet(glob)`**
— parameter/date-range/quality/source/platform/station equality-or-range
predicates map directly to `WHERE` clauses; the spatial bbox filter uses
DuckDB's `spatial` extension's `ST_Intersects`/`ST_MakeEnvelope`
(verified above), preserving the existing polygon-AOI-readiness. One
DuckDB connection (or a small pool) per worker process, reused across
requests rather than opened fresh per query — exact pooling strategy is
an implementation-time decision informed by the concurrency benchmark
(testing requirements below), not pre-committed here.

**7. Admin QC scanning's blind spot is addressed explicitly, not
silently left broken.** `admin_qc_service.py`'s three detectors
(duplicate/outlier/missing-values) query `DatasetRecord` exclusively
today — confirmed in the codebase survey that this already produces a
blind spot for any Zarr-backed gridded data (pre-existing, silent), and
this phase would otherwise silently extend that blind spot to *all* new
tabular data too, since new tabular files never get `DatasetRecord`
rows either. This phase adds DuckDB-Parquet equivalents of the same
three checks (duplicate `(time, station_id, parameter)` groups,
3σ outlier counts, declared-vs-actual row count) for `parquet`-backed
files, run by the same `run_qc_scan` orchestration, writing the same
`QualityIssue` rows — so QC coverage does not silently regress for new
data. This was not part of the narrower draft and is called out
specifically because it was found to be a real, unaddressed gap during
research for this rescoped plan.

**8. Admin Dashboard gains the per-file visibility item from the
earlier draft, extended with the new storage kinds** — per-dataset file
list (new, since none exists today) showing each file's format,
`storage_kind` badge ("📊 SQL Records (legacy)" / "📦 Parquet" / "🗂️
Chunked Array" / "🖼️ Raster/COG"), record/row count, and ingestion
status.

**9. Upload UI still makes no new admin-facing storage choice** — same
as the earlier draft's decision, unchanged: routing is fully automatic
from detected file shape; the per-file badge (item 8) is where the
automatic decision becomes visible after the fact.

### Backend changes
- `backend/app/services/parsers/base.py` — `DataShape.GRIDDED` added
  (as in the earlier draft); size-threshold constants removed from
  `netcdf_parser.py`/`mat_parser.py`, replaced by shape-based branching.
- `backend/app/services/parsers/netcdf_parser.py`,
  `mat_parser.py`, `mat_gridded_struct.py` — as described in the earlier
  draft's items 1-2, still needed: `parse()` sets `shape` explicitly;
  `_to_zarr()`/`_gridded_to_zarr()` rewritten for one-object-per-chunk
  layout.
- `backend/app/services/storage/keys.py` — new `processed_prefix()`.
- `backend/app/models/catalog.py` — `StorageKind` enum (now 4 values,
  not 3); `DatasetFile.storage_kind` column.
- `backend/alembic/versions/` — new migration: nullable `storage_kind`
  column + supporting index.
- `backend/app/worker/tasks/ingestion.py` — `_run_ingestion` sets
  `dataset_file.storage_kind`; the `_write_dataset_records` call is
  removed from the normal ingestion flow (not just conditionally
  skipped); `_write_variable_registry` gains a DuckDB-based numeric-
  range/distinct-value computation path for `parquet`-backed files
  (today it reads Parquet row-group stats directly via `pyarrow` for
  this — confirm whether that existing logic already works unmodified
  against the new artifacts, since it doesn't depend on `DatasetRecord`
  at all, only on the Parquet file itself; likely no change needed here,
  to be confirmed during implementation).
- **New** `backend/app/services/tabular_query_service.py` — DuckDB-based
  Parquet query layer (item 6).
- **New** `backend/app/services/gridded_query_service.py` — unchanged
  from the earlier draft (Zarr query layer).
- `backend/app/services/catalog_service.py`,
  `backend/app/services/visualize_service.py`,
  `backend/app/worker/tasks/visualize.py` (the sync-session duplicate of
  `get_spatial_points` used by the async spatial-interpolation Celery
  task — confirmed a second, independent copy of the same query logic
  that needs the identical routing treatment) — routing-layer additions
  as described in item 5.
- `backend/app/services/requests_service.py` — `get_coverage_for_request`
  already calls `catalog_service.get_matching_record_counts`, which
  gains the routing layer transparently; no direct change expected here,
  to be confirmed.
- `backend/app/services/admin_qc_service.py` — new DuckDB-Parquet
  detector implementations (item 7), dispatched alongside the existing
  `DatasetRecord`-based ones by `storage_kind`.
- `backend/app/services/admin_dataset_schema_service.py` — include
  `storage_kind` per file in the schema-review response.
- `backend/app/worker/tasks/extraction.py`, `backend/app/services/
  extractors/` — new Zarr-aware extractor (confirmed required, not
  optional — see Definition of Done): `_source_for_format()` gains a
  branch for `storage_kind=chunked_array` sources, opening the Zarr
  store via `xarray` instead of resolving one `processed_key`/
  `storage_key` object; a new `ZarrExtractor` (or equivalent) applying
  the same scope-filter semantics `scope_filter.py` already implements
  for tabular sources, registered in `extractors/registry.py` alongside
  the existing four.
- `backend/pyproject.toml` — add `duckdb` (verified installable via
  standard PyPI manylinux wheels into the existing `python:3.12-slim`
  Docker images with no new system libraries required — confirmed by
  installing and running it against this project's real backend
  container during research for this plan) and its `spatial`/`httpfs`
  extensions (auto-installed by DuckDB at first `INSTALL`/`LOAD` call,
  or pre-baked into the Docker image for faster cold starts — decided
  during implementation). Add `fsspec`+`s3fs` for the Zarr read path (or
  confirm `zarr`'s existing dependency tree already covers it — verify
  before adding).

### Frontend changes
- `frontend/src/app/admin/sections/AdminDatasetsSection.tsx` — new
  per-dataset file list with `storage_kind` badge (item 8).
- `frontend/src/app/admin/sections/AdminDatasetSchemaReviewSection.tsx`
  — read-only storage-kind indicator per file.
- `frontend/src/lib/types/catalog.ts`, `admin-datasets.ts` — new
  `storage_kind` field threaded through existing types.
- No change to the dataset-detail catalog filter UI, Visualize UI, or
  their underlying response-shape contracts — confirmed in the codebase
  survey that these are already storage-backend-agnostic on the frontend
  side; the routing happens entirely server-side.

### Database/migration changes
- `dataset_files.storage_kind` (new, nullable `VARCHAR`) + supporting
  index.
- One-time backfill script (`backend/app/scripts/backfill_storage_kind.py`)
  — labels existing files `row_records`/`chunked_array`/`raster` by
  best-effort inference from existing `file_metadata`, dry-run by
  default, matching existing script conventions. Writes no new data,
  labels only — `DatasetRecord` rows are never touched by this script.
- **No migration ever deletes or transforms `DatasetRecord` rows** —
  reiterated because it's the single most important non-negotiable
  constraint of this phase, given direct instruction not to destroy
  existing data.

### Storage/MinIO changes
- New multi-object Zarr layout (as in the earlier draft).
- New Parquet artifacts under `processed/{dataset_id}/{file_id}.parquet`
  (already the existing convention for tabular `to_processed()` output —
  no new layout needed here, confirmed these already work correctly
  with DuckDB's glob-based multi-file reads per the empirical
  verification above).
- Existing `raw/` layout, multipart upload, presigned URLs — unchanged.

### Testing requirements
- **Concurrency/load benchmark (new, required before any production
  claim)**: DuckDB query throughput/latency against real Parquet files
  in MinIO under simulated concurrent load (start at 50, then 200, then
  400-500 simulated concurrent filter/Visualize requests), measuring
  p50/p95/p99 latency, MinIO request rate, and backend CPU/RAM — this is
  the test that turns "should be fine" into an actual number. No claim
  about safe concurrent-user counts is made in this phase's final report
  without this benchmark's real results attached.
- Parquet-writer row-group configuration check — confirm production
  `to_processed()` Parquet output actually gets meaningful row-group
  pruning benefit (not just column-projection benefit) under realistic
  filter patterns, tuning row-group size if the default doesn't deliver
  it.
- Mixed-storage-kind dataset test: a `Dataset` with one `row_records`
  file and one `parquet` file, confirming `get_filtered_records`
  correctly unions both sources' results rather than dropping either.
- DuckDB spatial-filter regression test, directly comparable to the
  existing `test_catalog.py:408-448`
  `test_records_spatial_filter_matches_manual_bbox_subset` (which
  cross-checks the API against an independent manual PostGIS query) —
  new test cross-checks the DuckDB path against the same manual query,
  proving bbox-filter parity between the two backends.
- New QC-detector tests for the DuckDB-Parquet path (duplicate/outlier/
  missing-values), directly parallel to existing `test_admin_qc.py`'s
  `_seed_dataset_with_anomalies`-based assertions.
- Existing test files needing direct attention (per the research
  survey): `test_dataset_upload.py`'s `test_upload_populates_dataset_
  records`/`test_upload_timeless_csv_still_populates_records` (currently
  assert new CSV uploads produce `DatasetRecord` rows — these assertions
  describe *legacy-path* behavior post-Phase-5 and need a new-path
  equivalent asserting the same file now produces a queryable Parquet
  artifact with zero `DatasetRecord` rows instead); `test_admin_bulk_
  import.py`/`test_bulk_import.py` (assert bulk-imported files produce
  identical `DatasetRecord` results to single-file upload — same
  reframing needed); `test_dataset_variables.py`'s Zarr-backed-upload-
  produces-no-records test (already correctly asserts zero
  `DatasetRecord` rows for gridded data — extends unchanged, now joined
  by an equivalent assertion for `parquet`-backed tabular data).
- `test_dataset_record_writer.py`/`test_ingestion_benchmark.py`/
  `test_backfill_dataset_records.py` — kept exactly as-is, reframed only
  in their docstrings as testing the **legacy/administrative path**, not
  the primary ingestion path.
- Full existing backend suite must stay green for every file/test not
  listed above — `DatasetVariable`/schema-review/permission tests are
  storage-layer-agnostic and unaffected (confirmed in the survey).
- Live verification: re-ingest a real CSV file and a real gridded `.mat`
  file (e.g. `january_instantaneous.mat`) end-to-end post-rollout,
  confirm both get correct `storage_kind` values, zero `DatasetRecord`
  rows for either, correct catalog-filter and Visualize results for
  both, and confirm an existing pre-rollout dataset's filtering/
  Visualize behavior is byte-identical to before this phase shipped.

### Rollback/recovery
Highest-blast-radius items: (a) the Zarr storage-layout change (as in
the earlier draft — implement and test fully before any parser starts
writing it), and (b) removing `_write_dataset_records` from the live
ingestion flow (the single line of change with the largest behavioral
consequence in this whole phase — recommend landing and fully verifying
the DuckDB/Parquet query path FIRST, with `_write_dataset_records` still
also running in parallel as a temporary safety net for one verification
cycle, before actually removing the call — so a query-layer bug is
caught by comparing old-path and new-path results side by side rather
than discovered after the fallback no longer exists). No existing
`DatasetRecord` data is deleted or transformed at any point in this
phase, under any circumstance — pre-rollout data's query path is
entirely untouched code.

### What must NOT change in this phase
- Any existing `DatasetRecord` row, or the query behavior for any
  dataset ingested before this phase's rollout.
- `DatasetVariable`/schema review's role-assignment workflow — this
  phase only adds a read-only storage-kind field to its response.
- The CSV/NetCDF/.mat extractors' existing behavior for `row_records`-
  and raw-file-backed sources (`csv_extractor.py`/`netcdf_extractor.py`/
  `mat_extractor.py`/`parquet_extractor.py`, `scope_filter.py`) —
  unmodified for anything they already handle correctly today.
  **Correction from an earlier draft of this phase**: extraction is
  *not* uniformly unaffected — it never depended on `DatasetRecord`
  (true), but it does depend on `extraction.py`'s `_source_for_format()`
  resolving a single storage object, which a Zarr-backed source breaks;
  building a Zarr-aware extraction path is real in-scope work for
  this phase (see Definition of Done).
- The admin's upload flow itself (file picker, progress display,
  cancellation, minimize/background tracking) — no new pre-upload
  choice; routing stays fully automatic.
- GeoTIFF/raster handling — already correct, out of scope.
- Any decision about retiring/migrating existing `DatasetRecord`
  data — explicitly deferred to a future, separate, explicitly-approved
  phase, not part of this one.

### Definition of done — explicit, non-negotiable
**This phase is NOT complete merely because new ingestion stops writing
`DatasetRecord` rows.** That's a necessary precondition, not the
deliverable. The deliverable is: CSV, NetCDF, and MAT actual data reside
entirely in MinIO for every newly-ingested file, and every consumer of
that data — catalog filtering, Visualize, QC, and subset extraction/
download — genuinely works correctly against the new Parquet/Zarr
storage path, including for datasets that mix old (`DatasetRecord`) and
new (Parquet/Zarr) files. None of the items below is optional or
deferrable to "a later pass" within this phase; a phase-completion
report that checks only "no new SQL rows" against this list is
incomplete.

Every item below must be independently, live-verified — not inferred
from the mechanism being "structurally similar" to something that
already worked:

- [ ] **CSV**: a newly uploaded CSV file's data is stored only as
  Parquet in MinIO (zero `DatasetRecord` rows), and its catalog-detail
  filters (parameter/date/quality/source/platform/station/spatial bbox)
  return results verified equivalent to the legacy row-per-observation
  path on the same source data.
- [ ] **NetCDF**: same, for a genuinely gridded NetCDF file (Zarr) and,
  separately, a non-gridded/tabular-shaped NetCDF file (Parquet) — both
  shapes must be verified, not just one.
- [ ] **MAT**: same, for both a flat/tabular `.mat` file (Parquet) and a
  gridded `.mat` struct (Zarr) — including the real
  `january_instantaneous.mat` reference file specifically, end-to-end.
- [ ] **Catalog filtering** works correctly against Parquet (DuckDB) and
  Zarr (`xarray`) for every filter type the UI exposes today — not just
  a single smoke-tested predicate.
- [ ] **Visualization** (time series, spatial, comparison, statistics —
  all four modules) produces correct results against both new storage
  kinds, verified against hand-computable expected values the same way
  `test_visualize.py`'s existing fixtures already do.
- [ ] **QC scanning** (duplicate/outlier/missing-values, all three
  detectors) runs correctly against Parquet-backed data — confirmed
  finding real, seeded anomalies, not merely confirmed to not crash.
- [ ] **Subset extraction/download** is explicitly re-verified end-to-end
  against the new path, not assumed safe because it structurally reads
  raw files rather than `DatasetRecord`. **Confirmed during planning
  that a real gap exists here**, contradicting the earlier draft's
  "structurally unaffected" framing: `extraction.py`'s `_source_for_
  format()` resolves the extraction source as either one single-object
  `processed_key` (CSV/Parquet output formats) or the one raw
  `storage_key` (NetCDF/.mat output formats) — neither shape works for a
  Zarr-backed source, which has no single `processed_key` object at all
  (a multi-object prefix, per item 2's storage-layout change). This
  phase must therefore **build a Zarr-aware extraction path** (open the
  Zarr store via `xarray`, apply the same scope-filter semantics
  `scope_filter.py` already implements for the tabular extractors, write
  the requested output format) as real, in-scope work — not a
  discovered gap to defer. Verified: exporting a filtered CSV/Parquet
  subset from a Parquet-backed dataset produces correct output, AND
  exporting a filtered subset (any supported output format) from a
  Zarr-backed gridded dataset produces correct output via the new
  extraction path.
- [ ] **Mixed-storage-kind datasets**: a `Dataset` with at least one
  legacy `row_records` file and at least one new `parquet`/
  `chunked_array` file, verified correct (not just "does not error") for
  every one of the above — catalog filtering, Visualization, QC, and
  extraction all correctly combine/reflect both sources.
- [ ] **A pre-existing, untouched dataset's** catalog/Visualize/QC/
  extraction behavior is verified byte-identical to before this phase
  shipped — a regression here fails the phase regardless of how well
  the new path works.
- [ ] **Admin Dashboard** shows, per file, how it's stored and processed
  (`storage_kind` badge), live-verified in the browser, not just present
  in an API response.
- [ ] **The 400–500 concurrent-user benchmark** has been run against
  real infrastructure and its actual measured numbers (p50/p95/p99
  latency, MinIO request rate, CPU/RAM under load) are recorded in the
  phase's completion report. A phase report that asserts concurrency
  safety without these numbers attached does not satisfy this
  criterion — measurement is mandatory, not optional evidence.
- [ ] **No `DatasetRecord` row is deleted, migrated, or altered**
  anywhere in this phase, verified by row-count comparison before/after.

A phase-completion report must address every checkbox above explicitly
(pass/fail/not-applicable-with-reason) — silence on any item is treated
as not done, not as "presumably fine."

---

## 4. Cross-cutting requirements (apply to every phase)

- **Requirement 22, 23, 24** (preserve Phase 1–9, small uploads, and the
  Wave Data/Model Wave Data fixes) are not a phase — they are a
  standing constraint checked at the end of *every* phase via the full
  existing backend test suite plus a manual live-data check that
  `GET /catalog/{Model Wave Data id}/records?parameter=u10` still returns
  361,620 matches and Wave Data still returns its correct (currently
  zero, honestly) result.
- **Every phase ends with**: full backend test suite run (expect 414+N
  passed, the 1 pre-existing unrelated `test_me.py` failure excluded from
  blocking), a live Docker rebuild/restart, and an explicit written
  report of what changed and what passed/failed — matching the working
  pattern established across Sub-phases B and A.
- **No phase begins implementation without an explicit, separate approval**
  for that phase specifically — this document is the roadmap, not a
  blanket authorization to proceed through all 4 phases.

---

## 5. Summary table

| Phase | Name | Hard dependency on | New DB tables/columns | Highest risk item |
|---|---|---|---|---|
| 1 | Large Upload Infrastructure (+ Cancellation) | none (builds on existing storage interface) | `uploads` multipart-session columns, `UploadStatus` +2 | Cancellation racing a checkpoint mid-task |
| 2 | Large-File Processing, Automatic Variable Detection & Schema Registry | Phase 1 (bulk import reuses its put/multipart logic; cancellation checkpoints assumed present) | `dataset_records` 3 columns nullable, new `dataset_variables` table (detected variables, unreviewed) | NetCDF Dask chunk-axis correctness at scale |
| 3 | Admin Schema Review & Variable Role Assignment | Phase 2 (`dataset_variables` rows must already be detected/persisted) | `dataset_variables.role(s)`, review-state tracking | New permission scoping/UX consistency |
| 4 | Dynamic Filtering & Visualization | Phase 3 (needs approved roles to gate on) | none anticipated | Visualize rewrite blast radius (622-line file, 6+ call sites) |
| 5 | Storage & Query Architecture (No New Data Rows in PostgreSQL) | Phase 2/4 (extends the same parsers and query layer; needs `DatasetVariable`/schema-review's existing UI to attach the new read-only indicator to) | `dataset_files.storage_kind` (nullable) | Removing `_write_dataset_records` from the live ingestion flow without a verified-equivalent DuckDB/Zarr query path already proven correct first; unverified concurrency behavior at 400-500 users |

**Phases 1–4 and the gridded-MATLAB-struct extension of Phase 2 are implemented (see commit history). Phase 5 is planning only — not yet implemented — pending explicit approval to begin. Phase 5 was rescoped once (see its own "Status: RESCOPED" note) from a narrower gridded-data-only version to the current no-new-`DatasetRecord`-rows-at-all version; the narrower version was never implemented.**
