# Performance & Behavior Fix Plan — Visualize + Data Page (Gridded/NetCDF)

## Context

A prior investigation-only pass (no code changed) confirmed, with direct
reproduction and real timings against the live system, six distinct
issues affecting the Visualize module and Data Page, concentrated on
large Zarr-backed NetCDF datasets (real dataset "MY NetCDF": 19.6M
cells, `time:5479 × latitude:73 × longitude:49`, 6 variables). The user
reviewed that report and asked for a rigorous, standard, scientifically
sound implementation plan — not ad hoc fixes. This plan is that: it
separates the deployment gap, the functional bug, and the performance
regression, then fixes the *shared* root cause once so every consumer
(Data Page, Visualize coverage, Statistics) benefits identically,
finishing with a benchmark and full regression pass before anything is
committed.

Two additional facts discovered while preparing this plan (not in the
original report, verified just now):
- **`get_raw_values` in `gridded_query_service.py` is the single, precise
  fix location for the Comparison bug** — direct testing confirmed
  `get_spatial_points` and `get_timeseries_aggregate` already handle a
  variable with no `time` dimension safely (both return 200 OK for
  `elevation`); only `get_raw_values` crashes (`gridded_query_service.py:326`,
  `spatial_mean["time"].values`). This same function is called by
  **both** Comparison and Statistics — fixing it here resolves Statistics'
  own separate, previously-unreported `KeyError: 'time'` 500 for
  `elevation` too, not just the reported Comparison failure.
- **`backend`, `celery-worker`, and `celery-worker-ingestion` all build
  from the same `Dockerfile.dev`/`./backend` context** (confirmed in
  `docker-compose.yml`) — the missing-`s3fs` gap is purely a stale image
  that never got rebuilt after `s3fs` was added to `pyproject.toml`, not
  a genuine Dockerfile divergence.
- **MY NetCDF's native time resolution is exactly 1 day** (verified by
  reading `ds["time"].values` directly) — confirms the Statistics
  dedup fix (Phase 6) is safe for this dataset since `get_raw_values`'
  raw per-timestep series and `get_timeseries_aggregate(resolution="daily")`'s
  resampled series are equivalent when native resolution is already
  daily. This equivalence must be re-verified (not assumed) for any
  dataset with a coarser or irregular native resolution before treating
  the merge as universally safe.

## What this plan does NOT touch

- PostgreSQL/MinIO storage layout, Parquet↔DuckDB / Zarr↔xarray design.
- Phase A/B/C scientific corrections, or any of the still-uncommitted
  fixes from the immediately-prior "Visualization & Filter Reliability"
  task (coordinate-dialect fallback, MATLAB datenum handling,
  `is_dimension` parameter filtering, AOI/Custom-Boundary wiring, no-
  time-dimension guards already added to `get_timeseries`/`get_statistics`/
  `get_comparison`'s legacy-SQL paths). Those are separate, already-
  verified work; this plan only touches the *gridded* (`gridded_query_
  service.py`) side plus the coverage endpoint's own newly-introduced
  cost.
- No new backend dependencies, no schema/migration changes.

**Important sequencing note on `/visualize/coverage`:** this endpoint
is still uncommitted (introduced earlier this session, never shipped).
Rather than "fix a regression" as a separate follow-up, Phase 3/4 below
finish that endpoint correctly *before* it is ever committed — so the
eventual commit for that prior task includes a coverage endpoint that
was fast and cached from the start.

---

## Phase 1 — Fix the `s3fs` deployment gap

**Root cause:** `s3fs>=2024.10.0` is declared in `backend/pyproject.toml`
but the `celery-worker`/`celery-worker-ingestion` images are stale.

**Action:**
1. `docker compose build celery-worker celery-worker-ingestion celery-beat`
   (include `celery-beat` too — same image family, same risk, cheap to
   confirm even though not directly implicated in a report).
2. `docker compose up -d` to recreate the containers from the rebuilt
   images.
3. Verify: `docker exec <each container> python -c "import s3fs"` succeeds
   in all three.
4. Re-run the exact reproduction from the investigation (a Spatial
   Mapping request against MY NetCDF large enough to cross
   `VIZ_SPATIAL_SYNC_THRESHOLD_CELLS` into the Celery path) and confirm
   the job completes instead of failing with `No module named 's3fs'`.

**Risk:** None — pure infrastructure, no code change, fully reversible
(the containers already run this exact image spec, just stale).

---

## Phase 2 — Fix the gridded `get_raw_values` no-time-dimension bug

**Root cause (precise):** `gridded_query_service.py`, `get_raw_values`
(~line 314-326) checks `"time" not in ds.coords` (dataset-level) but
never checks whether the *selected variable itself* has `time` among
its own dims before doing `spatial_mean["time"].values`. A variable
like `elevation` (static, no time dimension) collapses to a 0-d array
via `.mean(dim=spatial_dims)` with no `time` coordinate left, raising
`KeyError: 'time'`.

**Fix:** After `data_array = filtered[parameter]`, check
`"time" not in data_array.dims` and return `[]` immediately (mirrors
the existing "no data for this shape" contract already used elsewhere
in this file, e.g. the `parameter not in ds.data_vars` early return
right above it) — a static variable simply has no time-series raw
values to contribute, exactly like a fully-filtered-out result already
returns `[]` today.

**Scope:** This single function is called from two places
(`visualize_service.py` — `get_comparison` and `get_statistics`, both
already confirmed via grep). No other gridded function needs this fix
— `get_spatial_points` and `get_timeseries_aggregate` were directly
tested against `elevation` and both already return 200 OK.

**Test matrix (both endpoints, both new backend tests and live
verification against MY NetCDF):**
- Comparison: `elevation`+`so`, `elevation`+`thetao`, `so`+`thetao`
  (already-working control case), and — if the dataset has a second
  static variable — static+static.
- Statistics: `elevation` alone (currently 500, must become 200 with
  `box_plot` still populated where meaningful and the time-dependent
  sections either empty or clearly flagged, consistent with the
  existing `has_temporal_data`-style pattern from the prior task).
- Regression: `so`, `thetao` (already-working variables) must still
  return identical results before/after.

---

## Phase 3 — Finish `/visualize/coverage` correctly (debounce + cache)

**Root cause:** `useVizFilters.ts` fires `postCoverage` (and
`postFilteredStations`) on every `[filters]` change with no debounce
and no request cancellation — confirmed by reading the effect bodies
directly. The Data Page's own `useDatasetFilters.ts` already debounces
its equivalent fetch by 250ms; Visualize's coverage effect does not.

**Fix (frontend, `useVizFilters.ts`):**
- Wrap the `postCoverage` (and, for consistency, `postFilteredStations`)
  effect body in the same `setTimeout(..., 250)` debounce pattern
  already used by `useDatasetFilters.ts`, with the existing `cancelled`
  flag plus `clearTimeout` in the cleanup — this is a direct copy of an
  already-proven pattern in this codebase, not a new mechanism.
- **Explicit decision, not assumed:** true in-flight request
  cancellation (`AbortController`) is a separate, larger change —
  `apiFetch` in `lib/api/client.ts` does not currently accept an
  `AbortSignal`. Debouncing alone eliminates the overwhelming majority
  of redundant calls (a user typing/adjusting a filter no longer fires
  one request per keystroke, only one ~250ms after they stop). Recommend
  debounce-only for this phase; add `AbortSignal` plumbing only if
  post-fix measurement still shows a real problem from rapid
  legitimate filter changes (e.g. dragging a date slider).

**Fix (backend, `visualize_service.py`, `get_visualize_coverage`):**
- Add the same Redis caching pattern already used by `get_timeseries`/
  `get_comparison`/`get_statistics` (`_cache_key`, `cache_get_json`,
  `cache_set_json`, `_CACHE_TTL_SECONDS = 300`) — currently entirely
  absent from this endpoint. This is a direct, mechanical reuse of an
  existing helper, not new caching infrastructure.

**Test matrix:**
- Rapid simulated filter changes (3+ changes within 250ms) → confirm
  exactly one backend call fires, not one per change.
- Identical repeated request → second call hits cache (assert response
  time drops, same pattern already used to verify Statistics' cache).
- Cache correctly varies by dataset/parameter/date range/bbox/AOI (i.e.
  the cache key must include every field that changes the result — this
  already exists via `_cache_key(params)` for the other 3 endpoints;
  confirm `get_visualize_coverage` uses the identical helper, not a
  hand-rolled key).

---

## Phase 4 — Shared gridded matching-count: exact always, made efficient

**User decision (confirmed):** `matching_count` stays exact always, no
size threshold, no estimate — scientific honesty over raw speed. The
tiered exact/estimate design considered during planning is dropped
entirely. This reframes Phase 4 around two things: (1) confirming there
is genuinely one shared implementation, not three, and (2) finding
whatever speedup is available *without* changing what's computed.

**(1) Already one shared implementation — verified, not assumed.** Both
the Data Page (`catalog_service.get_filtered_records` →
`gridded_query_service.get_filtered_records`) and `/visualize/coverage`
(`visualize_service.get_visualize_coverage` →
`catalog_service.get_matching_record_counts`) already delegate to the
same underlying `gridded_query_service.get_matching_record_counts`
(confirmed by reading both call chains). No duplicate implementation to
consolidate — this part of the original concern is already satisfied by
the existing architecture.

**(2) The real, safe lever: batch the per-variable count into one dask
compute pass.** Current code (`get_matching_record_counts`,
confirmed by reading it):
```python
matching = 0
for var in selected_vars:
    if var in filtered.data_vars:
        matching += int(filtered[var].count().values)  # separate .compute() per variable
```
Each `.count().values` triggers its own independent dask task-graph
execution — for a multi-variable selection (e.g. no `parameters` filter,
counting across all 6 variables), that's N sequential dask computations
instead of one batched one, each paying its own scheduling/dispatch
overhead even where chunk reads could otherwise overlap. Fix: build the
list of lazy `.count()` DataArrays first, then evaluate them together in
a single `dask.compute(*counts)` call (or equivalent `xr.combine`-based
reduction) — this changes *how* the exact count is computed, not *what*
is computed; results must be bit-identical before/after.

**Where the real relief actually comes from:** given the "exact always"
decision, Phase 3's Redis caching (already planned for `/visualize/
coverage`) and its extension to the Data Page (Phase 5) are what make
repeated/interactive use tolerable — the batched-compute change here is
a genuine but modest improvement to the *first* (uncached) computation,
not a replacement for caching.

**Test matrix:** hand-computable fixture, multi-variable selection —
assert `matching`/`total` are byte-identical before/after the batching
change; benchmark wall-clock for the uncached first call against MY
NetCDF's full 6-variable unfiltered case (the ~24.8s Data Page
reproduction) to quantify the batching improvement in isolation from
caching.

---

## Phase 5 — Data Page reuses the Phase 4 optimization

**Action:** No new logic — `catalog_service.get_filtered_records` /
`gridded_query_service.get_filtered_records` already call
`get_matching_record_counts` (confirmed, line ~383); once Phase 4 lands,
this path benefits automatically. Add Redis caching to the Data Page's
count/records path the same way (reuse `_cache_key`-style helper,
scoped by dataset+filters), since it currently has none.

**Explicit non-goal:** preview-row generation is already correctly
bounded/lazy (`.isel(time=slice(0,3))`, confirmed by reading the code)
— not touched, not the bottleneck.

**Test matrix:** repeat the exact 24.8s reproduction from the
investigation against MY NetCDF unfiltered, and again with a date/bbox
filter; confirm large measured improvement; confirm small dataset
timings are unaffected (no regression for already-fast cases).

---

## Phase 6 — Statistics: eliminate the duplicate Zarr read

**Root cause:** `get_statistics` independently calls `get_raw_values`
(for `box_plot`) and `_merged_timeseries` → `get_timeseries_aggregate`
(for histogram/annual_anomalies/decomposition/calendar_heatmap) against
the *same* file/parameter — two separate `_open_zarr()` + spatial-mean
computations reading the same underlying chunks twice.

**Fix:** Compute the gridded raw series once via `get_raw_values`, and
derive the histogram/anomaly/decomposition/calendar-heatmap series from
that same result instead of a second independent
`get_timeseries_aggregate` call — **conditional on verifying** (per the
Context section's caveat) that `get_raw_values`' native-resolution
series is equivalent to `get_timeseries_aggregate(resolution="daily")`'s
resampled series for the dataset under test. Where native resolution is
coarser/irregular, resampling logic must still run against the single
fetched series (in-memory, cheap) rather than re-fetching from Zarr.

**Test matrix:** MY NetCDF (daily-native, confirmed) before/after —
identical scientific results (box_plot, histogram, decomposition
values unchanged), single Zarr read confirmed (e.g. via a debug counter
or by checking `_open_zarr` call count during a test), wall-clock
drop from ~37s toward roughly the single-read cost (~5-7s based on the
investigation's Comparison timings for one variable).

---

## Phase 7 — Spatial Mapping default extent (not a performance fix)

**Root cause:** `SpatialMappingModule.tsx`'s hardcoded
`DEFAULT_BOUNDS = {lat_min:20.5, lat_max:23.0, lon_min:88.0, lon_max:92.5}`
feeds the interpolation request whenever no AOI is drawn, and the module
auto-fetches on mount — producing a rectangular image overlay over a
fixed Bangladesh-coast region regardless of the selected dataset's real
location.

**Fix:** Replace the fallback with the *selected dataset's* real
spatial extent — reuse the same temporal-extent-from-`DatasetFile`
pattern already implemented for time (this session's prior task) but
for lat/lon (`DatasetFile.spatial_extent` — confirmed to exist as a
PostGIS geometry column on `DatasetFile` from earlier reading of
`models/catalog.py`). Fall back to the existing hardcoded
`DEFAULT_BOUNDS` only when the dataset genuinely has no spatial extent
recorded — never silently show a rectangle for the wrong geography.

**Important:** this rectangle must remain purely a *display/query
default* for the interpolation grid — it must **not** be written into
`aoi` state or treated as a user-selected AOI (no side effects on
lat/lon filter fields, no `activeCount` increment), matching how it
behaves today (confirmed: `DEFAULT_BOUNDS` only feeds the request
`bounds` field, never touches `aoi`).

**Test matrix:** select MY NetCDF (dataset lat/lon extent, if
recorded, likely well outside the Bangladesh box) → confirm the default
interpolation view now centers on the dataset's real coverage, not
Bangladesh; select a dataset with no recorded spatial extent → confirm
the old hardcoded fallback still applies, unchanged.

---

## Phase 8 — Benchmark (before/after, fixed dataset set)

Confirm which real datasets fill each slot before starting (some may
need a quick inventory check, not assumed):
- **A — small tabular:** My Data or Bay of Bengal SST (CSV/Parquet).
- **B — small/medium gridded:** Gridded Query Test (~12M cells, Zarr,
  single "Tracer" variable) — already used as a benchmark case in the
  prior task.
- **C — large gridded, single dominant variable:** re-check for a
  mid-size Zarr dataset between B and D if one exists; otherwise B and D
  alone still cover the meaningful size range.
- **D — MY NetCDF:** 19.6M cells × 6 variables, the dataset used
  throughout this investigation.

Preserve the investigation's already-measured numbers as the "before"
baseline (Statistics cold ~37.0s / cached ~0.25s; coverage ~6.1s;
Comparison "so"+"thetao" ~8.6s; Data Page records unfiltered ~24.8s) —
no need to re-measure "before" from scratch. Measure "after" for the
same operations post-fix.

---

## Phase 9 — Regression testing

- **Backend:** full `pytest` suite against the isolated test DB
  (`docs/TESTING.md` pattern) — this also finally completes the full
  regression run left unfinished at the end of the prior task (repeated
  Docker Desktop instability prevented it from ever finishing; this is
  a good point to resolve that outstanding item alongside this work,
  not a new obligation).
- **Frontend:** `npx tsc --noEmit` + `npx eslint` on every touched file.
- **Live walkthrough:** every module (Temporal, Spatial Mapping,
  Multivariable, Statistics) × every dataset in the benchmark set,
  Data Page filtering for the same set, confirmed via direct backend
  calls and the running frontend — not UI-renders-without-erroring
  alone; check actual returned values against expectations, matching
  this whole session's established verification discipline.
- **Infrastructure:** confirm all three rebuilt containers
  (`backend`, `celery-worker`, `celery-worker-ingestion`) plus
  `celery-beat`, Redis, Postgres, MinIO all healthy post-rebuild.

---

## Execution order (one phase at a time, verify before proceeding)

```
1. s3fs deployment fix         (isolated, zero code risk)
2. get_raw_values fix          (isolated function, precise scope)
3. Coverage debounce + cache   (low-risk, immediate relief)
4. Shared gridded count: batch per-variable compute (exact-always, confirmed)
5. Data Page reuse + caching   (depends on 4)
6. Statistics dedup            (depends on 2's fix being in place first)
7. Spatial Mapping default extent  (independent, cosmetic/correctness only)
8. Benchmark                   (after all fixes)
9. Regression + full test suite
10. Present final report; commit only after user reviews it
```

After each phase: implement → test that phase specifically → confirm
no regression in already-passing areas → only then move to the next
phase. No commit until Phase 9 is fully green and the final report
(issue-by-issue, before/after timings, pass/fail summary) is presented
and reviewed — matching the standing discipline for this whole project.
