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

**No phase has been implemented. This file is the plan only.**
