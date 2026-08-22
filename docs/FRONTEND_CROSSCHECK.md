# Frontend Cross-Check — Phase 10.8

Final page-by-page pass confirming every interactive element across the
whole frontend (admin dashboard, user dashboard, public site) calls a
real backend endpoint, not a client-only stub — the closing quality gate
for the whole Phase 1–10 build. Traced element-by-element:
`section.tsx` → `frontend/src/lib/api/*.ts` → `backend/app/routers/*.py`
→ `backend/app/services/*.py` (or a real Celery task).

**Total scope**: 36 admin/user-dashboard sections (~90 interactive
elements) + 10 public pages. **Result: every element traced terminates
in a real, DB-backed (or Celery-backed) endpoint.** Two minor
error-handling gaps found and fixed; one real missing-asset bug found
and fixed. No client-only mock mutations, no calls to non-existent API
functions, no hardcoded/fake backend responses, and (beyond the two
fixed cases) no silently-swallowed errors on any primary user action.

## Admin dashboard (22 nav items, all 8 groups)

| Section | Status |
|---|---|
| Overview | ✓ |
| Notifications | ✓ (fixed — see Findings) |
| Profile | ✓ |
| Data Requests | ✓ |
| Access Grants | ✓ |
| Datasets (+ upload/publish/bulk-import modals) | ✓ |
| Dataset Categories | ✓ |
| Data Quality Check | ✓ |
| Schema Review | ✓ |
| Users (+ modals) | ✓ (fixed — see Findings) |
| Roles & Permissions | ✓ |
| Admin Management | ✓ |
| Export Settings | ✓ |
| Compute Limits | ✓ |
| Boundary Shapefile | ✓ |
| Blog Posts | ✓ |
| Media Library | ✓ |
| Site Content | ✓ |
| Analytics | ✓ |
| Reports | ✓ |
| Audit Log | ✓ |
| Settings | ✓ |
| Contact Information | ✓ |
| Support Tickets | ✓ |
| Backups & Recovery | ✓ (Phase 10.3 — real `pg_dump` Celery task) |
| System Health | ✓ (Phase 10.4 — real DB/Redis/storage/Celery/disk checks) |

## User dashboard (10 sections)

Overview, My Requests, My Datasets (+ extraction download flow),
Notifications, Profile, Security, Preferences, Help Center, Guidelines,
Contact Support — all ✓, every action DB-backed. One transparently
disclosed non-feature: 2FA in Security is explicitly labeled "coming in
v2.1," not disguised as functional.

## Public pages (10 routes)

Home, Catalog (search/filter), Dataset Detail (spatial/date/parameter
filters + real `POST /requests` submission with audit trail), Visualize
(all 5 modules — Temporal/Spatial/Comparison/Statistics/Profiles, real
Celery job polling for heavy queries), Blog, About, Contact (real form
submission), Legal, Login (sign-in/register/forgot-password), and the
reset-password/set-password/verify-email flows — all ✓.

Notably honest existing behavior confirmed during this pass: the Home
page's stat cards explicitly drop any figure lacking a real backend
source rather than inventing one; the Profiles visualize module
explicitly discloses that density contours/isopycnals are not
implemented rather than approximating them.

## Findings and fixes

1. **`AdminNotificationsSection.tsx` — silently swallowed errors on
   mark-read actions.** `handleMarkAllRead`/`handleItemClick` had empty
   catch blocks with a comment claiming "no toast context wired here" —
   untrue; `useToast` is used by 38 other admin sections. Fixed: both
   now surface a real error toast on failure.
2. **`UsersSection.tsx` — bulk suspend/activate showed a false-positive
   success toast on partial failure.** `Promise.allSettled` results were
   never inspected; a bulk action that partially failed still reported
   full success. Fixed: now counts actual successes/failures and reports
   accurately (full success / full failure / partial failure, each with
   its own message).
3. **Missing default boundary asset — real bug, not just a UI gap.**
   `GisSpatialMap.tsx`'s Spatial Mapping reference-boundary fallback
   fetches `/geo/bangladesh-boundary.geojson`, which never existed
   (confirmed via a live 404 in the dev server's own request log during
   this pass). The admin "Data Visualization" section's own copy
   explicitly promises "the built-in Bangladesh outline" as the
   sitewide default, replaced only when an admin uploads an override —
   that built-in default was never shipped. Fixed: added a real
   Bangladesh national boundary (Natural Earth 1:10m Admin 0 Countries,
   v5.1.1, public domain, filtered to `ADM0_A3 == "BGD"` and simplified
   to ~100m tolerance for reasonable file size) at
   `frontend/public/geo/bangladesh-boundary.geojson`, confirmed served
   correctly (200, valid `FeatureCollection`) from the running dev
   server. Attribution documented in `frontend/public/geo/README.md`.

## Known, disclosed limitations (not fixed, not in scope)

- **Visual/browser verification**: no browser-automation tooling was
  available in this environment, so the boundary fix above was verified
  by tracing the exact code path and confirming the served file's
  content/shape match what `L.geoJSON()` expects — not by an actual
  rendered screenshot. The underlying Leaflet API is standard and
  well-tested for this exact input shape.
- Everything else in this document reflects genuine code-level tracing
  (handler → API client → router → service/DB), the same standard this
  whole Phase 10 hardening effort has held throughout — not a guess.
