# BODP — Master Plan & Master Prompt

**Bangladesh Ocean/Environmental Data Platform — full-stack rebuild**

This document is the single source of truth for the project. It supersedes
`system_architecture.md.pdf` wherever the two disagree — the PDF was an early
sketch; this plan reflects the actual frontend prototype in `bodp-frontend/`
(which is the authoritative UI/UX/workflow reference) plus decisions made
with the project owner. Every phase below is self-contained: objectives,
scope, tasks, deliverables, quality checks, completion criteria.

**How this document is used going forward:** when the owner says "Complete
Phase N," implement everything listed under that phase, in order, against
this spec — do not re-litigate decisions already made here unless new
information contradicts them. If a phase turns up an ambiguity this document
doesn't resolve, ask before proceeding rather than guessing.

---

## 0. Foundational decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Backend framework | **FastAPI** (Python) | Async-native, fits I/O-bound workload (presigned URLs, job polling, streaming), same scientific Python stack (xarray/scipy/netCDF4/h5py) either way. Matches original architecture doc. |
| Frontend framework | **Next.js 16 / React 19** | Already a working prototype; rebuild UI logic against real APIs, keep the design system, layouts, and component structure. |
| Database | **PostgreSQL + PostGIS** | Spatial queries (bbox/polygon intersection against dataset extents) are core to catalog search and visualization. |
| Object storage | **S3-compatible API** (MinIO on VPS now; AWS S3 / Backblaze B2 / Cloudflare R2 / Wasabi later) | Provider undecided — storage service is built against the S3 API so the backend never changes when the provider does. |
| Background jobs | **Celery + Redis** | Heavy jobs (subset extraction, interpolation, NetCDF/.mat parsing, email) run async; light requests stay synchronous. |
| Request granularity | **Whole dataset, scoped by filters** (no file picker) | Matches the frontend exactly — parameter/date/spatial filters, not per-file selection. |
| RBAC | **Coarse role (user/admin) + enforced fine-grained permissions** | The 8-permission system already designed in the admin UI (`Approve Requests`, `Manage Users`, etc.) becomes real server-side authorization, not decorative. |
| Account creation | **Self-register + mandatory email verification**; no admin gate on the account itself | Standard practice for research-data platforms — access control happens per-dataset-request, not at signup. |
| Download delivery | **Filtered subset extraction** | Backend generates a real subset (by date range / spatial box / parameters) as a new file via background job, then presigns *that*. Matches "Matching Records" language already in the UI. Raw whole-file download is a fallback for small files. |
| Visualization compute | **Sync for light queries, Celery job + polling for heavy ones** | As per architecture doc. |
| Admin scope | **All ~25 admin sections in scope**, no deferral | Blog/CMS/Reports/System Health/Backup all get real backends across the 10 phases. |
| Email | Transactional email API, SMTP-compatible (Postmark/SES/Resend-shaped), swappable via config | Verification, request lifecycle, grant-expiry notices. |
| Hosting | VPS (sizing proposed in Phase 1) + external object storage for bulk data | 300–400GB hot/local, 10–20TB total across tiers. |
| Target load | ~500 concurrent users, no major lag | Drives async backend, connection pooling, caching, and query-optimized storage choices throughout. |

**Non-negotiable design principle carried from the architecture doc:** the
database holds **metadata only** — never raw scientific data. Storage
backend is abstracted behind one service interface so VPS-local and
cloud-tier files are indistinguishable to the rest of the system. This is
what makes the "10–20TB across two storage locations, presented as one
system" requirement achievable.

---

## 1. Data model (derived from the frontend prototype's real types)

This is what actually has to exist in Postgres — reconciled from
`src/lib/types/*.ts` across dataset/user/dashboard/admin/blog, deduplicating
the two parallel category systems and two parallel blog shapes found in the
prototype.

**Core catalog**
- `users` — id, email, password_hash, full_name, institution, phone, role (`user`|`admin`), status (`active`|`suspended`), email_verified_at, avatar_url, bio, research_area, created_at
- `roles` — id, name, description, permissions (array of the 8 permission keys)
- `user_roles` — user_id, role_id (a user can hold a fine-grained role in addition to coarse `role`)
- `dataset_categories` — id, name, description, color_tag (admin-managed taxonomy; the 6 seeded values are just initial rows, not a hardcoded enum)
- `datasets` — id, code, title, description, category_id, location, source, platforms[], parameters[] (or normalized `dataset_parameters` table), resolution, license, processing_levels[], formats[], status (`published`|`draft`|`archived`), spatial_extent (PostGIS geometry), temporal_start, temporal_end, record_count, created_by, created_at, updated_at
- `dataset_files` — id, dataset_id, file_name, storage_backend (`vps_minio`|`cloud`), storage_bucket, storage_key, file_format (`csv`|`netcdf`|`mat`|`geotiff`|...), file_size_bytes, checksum, spatial_extent, temporal_start, temporal_end, version, uploaded_by, uploaded_at
- `dataset_records` — normalized observation rows (id, dataset_id, time, lat, lon, depth_m, station_id, parameter, value, unit, quality_flag, processing_level, format, source) — used for catalog preview, filtering, and small/medium visualization queries. Large-format files (NetCDF etc.) are queried on-demand from the file itself for heavy visualization, not fully materialized into this table.
- `stations` — id, name, code, lat, lon, depth_m

**Request/grant lifecycle**
- `dataset_requests` — id, user_id, dataset_id, justification (min 50 chars enforced server-side too), search_criteria (JSONB: category/parameter/date range/spatial bounds, as captured from the catalog detail filters), supporting_document_file_id, status (`pending`|`approved`|`rejected`), submitted_at, reviewed_by, reviewed_at, admin_note
- `access_grants` — id, user_id, dataset_id, request_id, granted_by, granted_at, expires_at, status (`active`|`revoked`), scope (JSONB: the possibly-admin-narrowed filter criteria that bounds what the user can extract/download)
- `subset_extractions` — id, grant_id, requested_scope (JSONB), status (`queued`|`processing`|`complete`|`failed`), output_file_key, output_size_bytes, celery_task_id, created_at, completed_at
- `download_logs` — id, grant_id, user_id, subset_extraction_id, downloaded_at, ip_address

**Admin operational entities** (one table each, mirroring `admin.ts`)
- `audit_log` — id, actor_id, action, action_type (`approve`|`reject`|`revoke`|`user`|`dataset`|`content`|`login`), target, ip_address, created_at
- `uploads` — id, dataset_id, file_name, size_bytes, status (`queued`|`processing`|`complete`|`failed`), uploaded_by, uploaded_at
- `quality_issues` — id, dataset_id, issue_type, severity (`low`|`medium`|`high`), status (`open`|`resolved`|`ignored`), detail, detected_at
- `blog_posts` — id, title, category, tag_key, author_id, excerpt, content_html, status (`published`|`draft`), featured, featured_image_key, views, created_at, updated_at (single unified shape — replaces the prototype's two disconnected `BlogPost`/`BlogPostEntry` types)
- `media_files` — id, file_name, storage_key, size_bytes, mime_type, uploaded_by, uploaded_at
- `cms_blocks` — key (PK), page, label, value (and the public Home/About/Contact/Footer pages genuinely read from this table — fixing the prototype's disconnect where Footer/CMS were decorative)
- `about_team_members` — id, name, role, bio, photo_key, display_order
- `admin_team_members` — id, user_id, role_label, status, last_active_at (staff-facing "who has admin access," distinct from `roles`)
- `reports` — id, type, date_range, generated_by, generated_at, output_file_key
- `backups` — id, started_at, size_bytes, status (`success`|`failed`), storage_key
- `notifications` — id, user_id, type, title, description, unread, created_at (unifies user-facing and admin-facing notification lists into one table, scoped by user_id/role)
- `support_tickets` — id, user_id, subject, category, priority, status, message, created_at
- `site_settings` — single-row config table (site name, contact emails, max upload size, session lifetime, notification toggles)
- `boundary_shapefiles` — id, name, geojson, uploaded_by, uploaded_at, is_default (backs the spatial mapping boundary feature)
- `failed_logins` — id, email_attempted, ip_address, reason, created_at
- `active_sessions` — id, user_id, device, ip_address, location, last_active_at, is_current (real session tracking, not mocked)

This list is the contract for Phase 1's migrations. Field names above are
intentionally close to the prototype's TypeScript types so API responses can
map to existing component props with minimal frontend rework.

---

## 2. Architecture summary

```
Browser (Next.js SSR/CSR)
        │ HTTPS
Nginx (reverse proxy, TLS via Let's Encrypt, rate limiting)
        │
        ├── Next.js frontend (Node process)
        ├── FastAPI backend (Uvicorn/Gunicorn workers)
        └── MinIO console (admin-only, internal)
                │
        ┌───────┼────────────┬──────────────┐
   PostgreSQL   Redis    Celery workers   MinIO (VPS-local tier)
   + PostGIS  (cache/    (background        + Cloud object storage
   (metadata   queue)     jobs: subset       (bulk tier, S3-compatible)
    only)                 extraction,
                          interpolation,
                          NetCDF/.mat
                          parsing, email)
```

Storage abstraction: one `StorageService` interface with two configured
backends (`vps_minio`, `cloud`) selected per-file via `dataset_files.storage_backend`.
Every read/write goes through this interface — nothing in the API or worker
code ever hardcodes a storage location. This is what lets "some data on the
VPS, most in the cloud" be invisible to the frontend.

---

## 3. The 10 phases

Each phase lists: **Objective**, **Scope**, **Tasks**, **Deliverables**,
**Quality checks**, **Completion criteria**. Phases are ordered so each one
only depends on what came before.

---

### Phase 1 — Foundations: infrastructure, database, auth

**Objective:** stand up the skeleton everything else builds on — repo
structure, database schema, authentication, and deployable dev environment.

**Scope:** no user-facing features yet beyond login/register/verify.

**Tasks:**
1. Repo structure: `backend/` (FastAPI: `routers/`, `services/`, `models/`, `schemas/`, `worker/`, `core/` for config/security), keep `bodp-frontend/` as `frontend/` or in place.
2. `docker-compose.yml`: postgres+postgis, redis, minio, backend, celery-worker, frontend, nginx — dev and prod variants.
3. Postgres schema via Alembic migrations, implementing every table in §1.
4. Auth: password hashing (bcrypt/argon2), JWT access + refresh tokens, email verification flow (signed token, expiring link), login/logout/refresh endpoints.
5. RBAC middleware: dependency-injected permission checks in FastAPI (`require_permission("Approve Requests")`), backed by `roles`/`user_roles`.
6. Rate limiting (Nginx + slowapi/FastAPI middleware) on auth endpoints specifically.
7. Structured logging + error handling conventions (Pydantic validation errors → consistent JSON error shape).
8. VPS sizing recommendation (see §4) and provisioning checklist (domain, DNS, SSL cert automation, firewall rules — only 443/80 and SSH exposed, internal Docker network for everything else).
9. Environment/secrets management (`.env` + secrets not committed; document required vars).

**Deliverables:** running `docker-compose up` dev stack; Alembic migration history; working `/auth/register`, `/auth/verify-email`, `/auth/login`, `/auth/refresh`, `/auth/logout`; permission-check dependency usable by later routers.

**Quality checks:** migrations apply cleanly on a fresh DB; JWT rejected when expired/tampered; a suspended user cannot obtain a new token; permission dependency denies access when the permission is absent even for a logged-in admin.

**Completion criteria:** can register → verify → log in → receive a role-correct token, entirely through real endpoints (no mocked auth), with automated tests covering the happy path and the main failure modes (bad password, unverified email, suspended account).

---

### Phase 2 — Storage abstraction & scientific file ingestion

**Objective:** the `StorageService` and file-processing pipeline that every
later phase depends on.

**Scope:** upload, store, checksum, and extract metadata from CSV, NetCDF,
`.mat`, and generic files, across both storage tiers.

**Tasks:**
1. `StorageService` interface (`put`, `get`, `presign_get`, `presign_put`, `delete`, `exists`) with `MinIOBackend` and generic `S3CompatibleBackend` implementations (boto3-based, works for any S3-compatible provider).
2. Bucket/key layout: `raw/{dataset_id}/{file_id}_{name}`, `processed/{dataset_id}/{file_id}.parquet`, `extracts/{grant_id}/{extraction_id}.{ext}`, `previews/{dataset_id}/thumbnail.png`.
3. File-format parsers as isolated services: `csv_parser` (pandas), `netcdf_parser` (xarray/netCDF4 — extract variables, dimensions, spatial/temporal extent), `mat_parser` (scipy.io/h5py, handling both legacy and v7.3 HDF5-based `.mat`), `geotiff_parser` (rasterio — raster/imagery data, re-tiled to a Cloud-Optimized GeoTIFF for the processed/ tier), all behind an extensible `FileParser` registry (`app/services/parsers/`). Adding a future single-file format (generic HDF5, etc.) is one subclass + one registry line — demonstrated by GeoTIFF's addition after the initial three formats. **Zarr is explicitly out of scope for this registry**: a Zarr store is a directory of many chunk objects, not one file, so it needs an upload/storage-layer change (a `raw_prefix()`-style multi-object key, or accepting a `.zarr.zip` container as the simpler bridge) before a ZarrParser can be added — see `FileParser`'s docstring in `app/services/parsers/base.py` for the concrete plan.
4. Celery task: on upload, parse → auto-detect spatial/temporal extent + variables (or bands, for raster formats) → write a query-optimized `processed/` copy (Parquet for tabular formats, re-tiled COG for raster) via the parser's `to_processed()` method → update `dataset_files`/`datasets` rows. `ParsedFileMetadata.shape` (tabular vs. raster) lets the ingestion task and later catalog/visualize code branch on shape without per-format knowledge.
5. Checksum (SHA-256) computed on upload, stored, verified before serving.
6. Malware/type validation on upload (reject mismatched extension vs. actual content, size caps per `site_settings.max_upload_size_mb`).
7. Admin upload UI wiring (`DataUploadSection`, `AddDataToDatasetModal`) → real endpoints, with the existing processing-status UI (queued/processing/complete/failed) now reflecting real Celery task state instead of a timer.

**Deliverables:** `POST /admin/datasets/{id}/files` (upload), background processing pipeline, real status polling endpoint.

**Quality checks:** a NetCDF file's declared spatial/temporal extent matches what's auto-detected; a corrupted/mismatched file is rejected with a clear error, not silently accepted; large file upload doesn't block the request thread (chunked/streaming upload).

**Completion criteria:** admin can upload a real CSV, NetCDF, and `.mat` file through the (now-real) Data Upload section and see accurate auto-extracted metadata land in the database, stored in the configured backend, retrievable via presigned URL.

---

### Phase 3 — Dataset catalog (public-facing, read side)

**Objective:** rebuild `/catalog` and `/catalog/[id]` against real data and real PostGIS-backed search.

**Scope:** browsing, search, filtering, dataset detail, data preview — matching the prototype's UI exactly, minus the mock data.

**Tasks:**
1. `GET /catalog/search` — category/parameter/source/platform/format/free-text filters, relevance scoring equivalent to the prototype's `matchScore` weighting, sort options (relevance/title/updated/records), backed by Postgres full-text + PostGIS where spatial is involved.
2. `GET /catalog/{id}` — dataset detail with meta, available parameters/platforms/processing levels.
3. `GET /catalog/{id}/records` — filtered preview query (bbox/polygon via PostGIS `ST_Intersects`, date range, depth range, parameter, quality flag, processing level, station) against `dataset_records`, capped preview (6 rows, matching UI) + full matching-count + per-quality-flag breakdown for the summary strip.
4. Category/parameter/source/platform/format list endpoints to populate filter dropdowns dynamically (replacing the prototype's hardcoded taxonomy).
5. Frontend: replace `useDatasetCatalog`/`useDatasetFilters` mock logic with real API calls; keep all existing components (`DatasetCard`, `SpatialFilterMap`, filter sidebar) as-is, swapping data source only.
6. Caching: Redis cache for filter-dropdown option lists and popular searches (these change rarely, hit often — matters at 500 concurrent users).

**Deliverables:** fully working, real-data catalog list + detail pages, indistinguishable in behavior from the prototype.

**Quality checks:** spatial filter (drawn rectangle) returns the same records a manual PostGIS query would; search relevance ordering matches the specified weighting; draft/archived datasets never appear in public results; response times stay low under concurrent load (index on `spatial_extent` via GIST, confirmed with `EXPLAIN ANALYZE`).

**Completion criteria:** the live catalog, browsed anonymously, looks and behaves exactly like the prototype, backed entirely by real Postgres data seeded via Phase 2's ingestion pipeline.

---

### Phase 4 — Request → approval → grant lifecycle

**Objective:** the core workflow the whole platform is built around — implemented end-to-end for the first time (the prototype never actually connected these two sides).

**Scope:** dataset request submission, admin review queue, approve/modify/reject, grant creation/extension/revocation.

**Tasks:**
1. `POST /requests` — from the dataset detail page's "Request Access" modal: full name, email, institution, justification (server-enforced 50-char minimum), optional supporting document upload, and the user's active filter state captured as `search_criteria`. Creates a real `dataset_requests` row, `status='pending'`.
2. Notification on submit: in-app `notifications` row for relevant admins + email.
3. `GET /admin/requests` — tabs/filters (all/pending/approved/rejected), matching `AdminRequestsSection`.
4. `POST /admin/requests/{id}/approve` — supports both "Approve All" (all requested scope, no changes) and "Modify & Approve" (admin edits `search_criteria`, narrows scope) → creates `access_grants` row(s), sets duration via the same 5d/10d/1m/2m/6m/1y/custom options as `GrantDurationModal`, computes `expires_at`.
5. `POST /admin/requests/{id}/reject` — requires non-empty reason, sets `admin_note`.
6. `POST /admin/grants/{id}/extend` and `POST /admin/grants/{id}/revoke` — matching `GrantsSection` actions; extend recomputes from *current* expiry, not from today (preserve this exact prototype behavior — it's correct).
7. Every mutation writes an `audit_log` row (actor, action_type, target, ip) — the audit trail is a hard requirement carried from the prototype's design, not optional.
8. User-side: `GET /me/requests` (status + 4-step progress matching `RequestsSection`'s stepper), `GET /me/grants` (feeds `MyDatasetsSection`).
9. Email notifications at each transition (submitted, approved, rejected, grant expiring soon — the last one via a scheduled Celery beat task checking `expires_at` daily).
10. Frontend: wire `DatasetRequestModal`, `AdminRequestsSection`, `ModifyApproveModal`, `RejectRequestModal`, `GrantDurationModal`, `RequestsSection`, `MyDatasetsSection`, `GrantsSection` to real endpoints, removing all local-state-only mutation.

**Deliverables:** a request submitted by a real user appears in that user's dashboard AND the admin dashboard, and admin actions on it are reflected back to the user — the exact requirement stated at the start of this project.

**Quality checks:** a rejected request cannot be re-approved without a new request; revoking a grant immediately invalidates any in-flight subset-extraction/download for it; permission-gated (`Approve Requests`) — an admin without that permission gets 403, not just a hidden button.

**Completion criteria:** full request lifecycle works with two real logged-in accounts (one user, one admin) across two sessions, with correct audit trail and email notifications observed.

---

### Phase 5 — Subset extraction & secure download delivery

**Objective:** turn an approved grant into an actual downloadable file, generated on demand from the real data, not the raw original.

**Scope:** the extraction engine and the presigned-download flow.

**Tasks:**
1. `POST /me/grants/{id}/extract` — validates the requested scope is within the grant's allowed scope, creates a `subset_extractions` row (`status='queued'`), dispatches a Celery task.
2. Extraction workers per format: CSV/Parquet → filter with DuckDB/pandas and re-serialize; NetCDF → `xarray` spatial/temporal/variable subsetting, re-encode as NetCDF; `.mat` → filtered re-save; multi-file datasets → zip bundle. Each respects the grant's scope (never allow extracting outside approved bounds, enforced server-side, not just client-side).
3. Status polling endpoint (`GET /me/extractions/{id}`) — queued/processing/complete/failed, matching the async job pattern already used for heavy visualization.
4. On completion: presigned `GET` URL (short expiry, e.g. 1 hour, configurable), `download_logs` row written on actual retrieval (not on presign generation) via a lightweight redirect/tracking endpoint in front of the presigned URL.
5. Small-file fast path: if the resulting subset is under a size threshold, skip the queue and extract synchronously.
6. Frontend: `MyDatasetsSection`'s "Download" button and `DatasetDetailModal` now trigger real extraction + poll + real file download instead of a toast.

**Deliverables:** working end-to-end "approved → extract → download" flow with real files.

**Quality checks:** requesting a scope outside the grant's approved bounds is rejected server-side; expired grants cannot start new extractions; presigned URLs actually expire; a large NetCDF extraction doesn't block other API requests (confirmed under load).

**Completion criteria:** a test user with an approved, scoped grant can request an extraction, watch it process, and download a correctly-filtered real file.

---

### Phase 6 — User dashboard (remaining sections)

**Objective:** complete every remaining `/dashboard` section against real data.

**Scope:** Overview, Notifications, Profile, Security, Preferences, Help Center, Guidelines, Contact Support.

**Tasks:**
1. `GET /me/overview` — real stat aggregation (approved/pending counts from actual `dataset_requests`, real download count from `download_logs`, storage usage if meaningfully trackable, recent activity from a real activity feed rather than hardcoded entries).
2. `GET/PATCH /me/profile` — real persistence (name, institution, research area, bio, avatar upload to storage not base64-in-localStorage).
3. `POST /me/change-password`, `GET/DELETE /me/sessions` (real `active_sessions` tracking, real revoke), account-deletion request flow (soft-delete with grace period, matching the "Danger Zone" UX).
4. `GET/PATCH /me/preferences` — real persistence of notification toggles, theme (or keep theme client-only per Phase 0 decision), date format/coordinate format (and actually apply them where dates/coords render, closing the prototype's gap).
5. `GET/PATCH /me/notifications`, mark-read/mark-all-read against the unified `notifications` table.
6. `POST /me/support-tickets`, `GET /me/support-tickets` — real persistence, admin-visible (feeds a future admin ticket view if desired, otherwise at minimum emails the support team).
7. Guidelines/Help Center: content served from `cms_blocks` (admin-editable) rather than hardcoded, so it's consistent with Phase 9's CMS work — or hardcoded now and migrated in Phase 9, whichever is more efficient in sequence (defer final call to implementation time).

**Deliverables:** all dashboard sections fully functional against real backend state, no more toast-only fake actions.

**Quality checks:** profile changes persist across logout/login; notification unread counts match badge counts shown in the sidebar; session list reflects actual active JWT/refresh-token sessions and revoke actually invalidates them.

**Completion criteria:** a user can manage their entire account through the dashboard with zero mocked interactions remaining.

---

### Phase 7 — Visualization engine

**Objective:** rebuild `/visualize`'s 4 modules against real queryable data and a real interpolation backend.

**Scope:** Temporal Analysis, Spatial Mapping (with IDW interpolation — kriging/nearest-neighbour can stay UI-present-but-labeled-unavailable exactly as the prototype does, unless you want them implemented for real, which is a larger scope call to make at that time), Multi-Variable Comparison, Statistics.

**Tasks:**
1. `POST /visualize/timeseries` — real aggregation from `dataset_records` (or from processed Parquet files for large series) by parameter/station/date range, server-computed trend line, moving average, seasonal (Bangladesh 4-season) breakdown, monthly climatology, rate-of-change, anomaly-from-mean — replacing the prototype's synthetic `genTimeSeries`.
2. `POST /visualize/spatial` — light requests (small bbox/point count) computed synchronously; heavy requests (large area, high resolution) dispatched as a Celery job using `scipy`/`numpy` IDW (and real kriging via `pykrige` if you want that option to actually work rather than stay a stub — flag this as an open question at Phase 7 kickoff), rasterized server-side, returned as GeoTIFF/PNG matching the existing Leaflet `ImageOverlay` rendering path. Job-id + polling pattern preserved from the architecture doc.
3. `POST /visualize/comparison` — real paired-series extraction for scatter/regression/correlation-matrix (Pearson r computed server-side against real data, not synthetic).
4. `POST /visualize/statistics` — box plots per station, histograms, annual anomalies, time-series decomposition, calendar heatmap, all from real aggregated queries.
5. Boundary shapefile: `boundary_shapefiles` table backs both the admin uploader (`DataVisualizationSection`) and the public map's clip boundary — genuinely shared, as it already correctly is in the prototype.
6. Caching layer: Redis cache for repeated identical viz queries (common with shared default filters at 500 concurrent users) with sensible TTL.
7. Frontend: `useVizFilters`, all 4 modules under `visualize/modules/`, `GisSpatialMap`, `SpatialFilterMap` wired to real endpoints; keep all existing chart/toolbar/fullscreen components unchanged.

**Deliverables:** every chart and map in the Visualize page backed by real computed data.

**Quality checks:** IDW output values sanity-checked against a known small hand-computed case; heavy job doesn't block the API event loop; boundary clipping and AOI clipping both verified visually against known geometries; large-area high-resolution requests don't exhaust worker memory (bounded grid size, documented limits).

**Completion criteria:** visualize page fully real-data-driven, heavy computations offloaded, response times acceptable under concurrent load testing.

---

### Phase 8 — Admin: dataset & user management

**Objective:** complete the admin sections that manage the platform's core entities (not content/reporting — that's Phase 9).

**Scope:** Overview, Users, Roles & Permissions, Access Grants (endpoints already built in Phase 4, UI wiring here), Datasets, Dataset Categories, Data Quality Check, Admin Management.

**Tasks:**
1. `AdminDatasetsSection`/`DatasetModal`/`AddDataToDatasetModal` → real CRUD against `datasets`/`dataset_files` (built on Phase 2's ingestion), publish/unpublish, soft-delete with grant-cascade-revoke exactly as the prototype specifies, real delete with storage cleanup (no orphan files — delete from storage backend before/alongside the DB row).
2. `UsersSection`/`UserModal`/`UserDetailModal` → real user CRUD, suspend/activate (suspension actually blocks login — enforce in Phase 1's auth layer, verify here), CSV export from real data, per-user grant history and revoke-from-detail-view wired to Phase 4's grant endpoints.
3. `RolesPermissionsSection`/`RoleModal` → real CRUD against `roles`, permission checklist genuinely drives the Phase 1 authorization dependency (this is where "make permissions real" from the locked decisions gets fully wired end-to-end).
4. `DatasetCategoriesSection`/`CategoryModal` → real CRUD against `dataset_categories`; deleting a category with datasets attached actually reassigns/nullifies `datasets.category_id` (fixing the prototype's soft/non-cascading gap).
5. `DataQualityCheckSection` → `POST /admin/qc/scan` becomes a real check (schema validation, null/outlier detection against `dataset_records`/processed files) producing real `quality_issues`, not a no-op.
6. `AdminManagementSection`/`AdminTeamModal` → real CRUD against `admin_team_members`, tied to real `users` with `role='admin'`.
7. Admin `OverviewSection` → every stat card and chart sourced from real aggregation queries (no hardcoded numbers).
8. Admin `AdminNotificationsSection` → unified with Phase 6's `notifications` table, scoped to admin recipients.

**Deliverables:** every admin data-management section fully real.

**Quality checks:** deleting a dataset with active grants correctly revokes them and the affected users see it reflected; permission enforcement verified per-role (create a low-privilege admin role, confirm it's actually blocked from disallowed actions via API, not just UI).

**Completion criteria:** an admin can fully manage datasets, users, roles, and categories with real, persisted, auditable effects.

---

### Phase 9 — Admin: content, analytics & reporting

**Objective:** complete the remaining admin sections — CMS, blog, media, analytics, reports, audit log.

**Scope:** Blog Posts, Media Library, Site Content (CMS), Data Visualization (boundary admin — endpoint already exists from Phase 7, UI here), Analytics, Reports, Audit Log, Settings.

**Tasks:**
1. `BlogPostsSection`/`BlogPostModal` → real CRUD against the **unified** `blog_posts` table; critically, `/blog` (public) now reads from this same table — closing the prototype's biggest content gap (admin edits will actually appear on the live site).
2. `MediaLibrarySection` → real file storage (via Phase 2's `StorageService`, not fake filenames), real delete.
3. `SiteContentSection`/`CmsBlockModal`/`AdminAboutTeamModal` → real CRUD against `cms_blocks`/`about_team_members`; **Home/About/Contact/Footer public pages read from `cms_blocks`** where the prototype's static copy currently lives — closing the Footer/CMS disconnect noted during frontend analysis. About page team section already has a correct live pattern in the prototype (`AdminDataContext.aboutTeam` → public page) — extend that same pattern to the rest.
4. `AnalyticsSection` → real aggregation (requests/approvals/downloads/new-users by real date-range queries), replacing synthetic growth curves.
5. `ReportsSection` → `POST /admin/reports` generates a real CSV/export (Usage Summary, Dataset Inventory, User Activity, Access Grants) from live data via a Celery job for larger exports, stored and presigned for download — replacing the prototype's trivial stub CSV.
6. `AuditLogSection` → already populated by every mutation since Phase 1; build the search/filter/paginate/export UI against the real `audit_log` table (CSV export respects the active filter, fixing the prototype's noted inconsistency).
7. `SettingsSection` → real persistence to `site_settings`, admin profile updates actually persisted.

**Deliverables:** all content/reporting/audit admin sections real and, where relevant, genuinely connected to the public site.

**Quality checks:** editing a CMS block updates the public page on next load; blog post published in admin appears on `/blog` immediately; audit log CSV export matches the active filter state.

**Completion criteria:** no remaining disconnected mock flows anywhere in the admin dashboard — every admin action has a real, visible, correct effect somewhere in the system.

---

### Phase 10 — Hardening: security, performance, reliability, launch readiness

**Objective:** the platform survives real traffic, real attackers, and real operational failure modes.

**Scope:** cross-cutting, applies to everything built in Phases 1–9.

**Tasks:**
1. **Security audit pass:** OWASP Top 10 review of every endpoint (injection, broken auth, XSS in blog/CMS HTML content — sanitize rendered HTML server-side since the prototype uses raw `dangerouslySetInnerHTML`, broken access control, SSRF via any URL-accepting field, insecure deserialization, security misconfiguration). Verify presigned URLs can't be scope-escalated. Verify file upload can't be used for path traversal into storage keys.
2. **Rate limiting** tuned per-endpoint (auth stricter than browsing), matching the `SecuritySection`'s configurable rate-limit UI (make those settings real, feeding actual Nginx/FastAPI middleware config).
3. **Load testing** at ~500 concurrent simulated users (k6/Locust) against catalog search, request submission, and visualization endpoints; tune Postgres connection pooling (PgBouncer if needed), Uvicorn/Gunicorn worker counts, Redis cache hit rates.
4. **Backups:** real automated `pg_dump` + storage-tier backup jobs feeding the (currently stub) `BackupRecoverySection` — "Run Backup Now" and scheduled backups actually produce recoverable artifacts; test an actual restore, not just log an audit entry.
5. **Monitoring/health:** `SystemHealthSection` wired to real subsystem health checks (DB connectivity, Redis, storage backend reachability, Celery worker liveness, disk usage) rather than hardcoded 96%.
6. **HTTPS/TLS** via Let's Encrypt with auto-renewal, HSTS, secure cookie flags, CSP headers appropriate for the Next.js app.
7. **Secrets rotation** process documented; no secrets in the repo (verified via a final grep/audit).
8. **Data integrity:** verify checksum validation is enforced on every stored file, orphan-file sweep job (files in storage with no matching DB row, and vice versa).
9. **Failed-login lockout** (`SecuritySection`'s "lock after 5 failed attempts") made real, backed by `failed_logins`.
10. **Documentation:** deployment runbook, environment variable reference, disaster-recovery steps, and a final `README` covering how to operate the production stack.
11. **Final cross-check against the frontend prototype:** a page-by-page pass confirming every interactive element from the original prototype (button, filter, modal, table action) now has a real, working backend behind it — using the earlier frontend functional map as the checklist.

**Deliverables:** a production-hardened system with verified backups, monitoring, and a documented operational runbook.

**Quality checks:** load test results within acceptable latency at target concurrency; a simulated backup-restore succeeds; a security review finds no critical/high findings outstanding.

**Completion criteria:** the system is ready to serve real users and real research data in production.

---

## 4. VPS sizing (proposed, to be confirmed against actual chosen provider in Phase 1)

For ~500 concurrent users, 300–400GB local hot storage, Dockerized stack
(Postgres+PostGIS, Redis, Celery workers, MinIO, FastAPI, Next.js, Nginx):

- **CPU:** 8 vCPU minimum (interpolation/subsetting jobs are CPU-bound; more cores lets Celery scale worker concurrency without starving the API).
- **RAM:** 32GB minimum (Postgres buffer cache + Celery workers processing NetCDF/large CSV in memory + Next.js/FastAPI processes). 64GB is safer headroom if budget allows, especially once kriging/large-grid interpolation is in active use.
- **Disk:** NVMe SSD, 500GB+ (300–400GB for the local storage tier + OS/Docker images/Postgres/logs/backups headroom). Separate volume for Postgres data recommended for I/O isolation.
- **Network:** provider with generous/unmetered bandwidth — downloads and cloud-tier proxying are bandwidth-heavy at scale.
- **Bump path:** if Celery workers become the bottleneck under load testing (Phase 10), scale horizontally with a second worker-only VPS before vertically scaling the primary — cheaper and matches the architecture doc's stated scaling roadmap.

---

## 5. Open items to revisit at the relevant phase (not blocking start)

- **Kriging/Nearest-Neighbour interpolation**: prototype has them as UI options that silently fall back to IDW. Decide at Phase 7 whether to implement for real (via `pykrige`) or keep the same honest "computed as IDW" fallback.
- **Object storage provider selection**: revisit before Phase 2 ends, once real pricing/egress comparisons are wanted.
- **Email provider selection**: pick a concrete provider (Postmark/SES/Resend/etc.) at Phase 1, config-level choice only.
- **Guidelines/Help Center content source**: hardcoded vs. CMS-driven — decide at Phase 6 or defer to Phase 9's CMS unification pass.
- **Zarr support**: not implemented (GeoTIFF is, as of Phase 2). The `FileParser`/`StorageService` architecture is deliberately shaped so adding it later doesn't require reworking either — but it does need a small upload/storage-layer addition first (multi-object/prefix upload, or a `.zarr.zip` container as the zero-storage-change bridge) before a `ZarrParser` can be registered. Revisit if/when a real Zarr dataset needs ingesting.
