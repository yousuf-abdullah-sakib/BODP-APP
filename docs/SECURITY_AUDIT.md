# Security Audit — Phase 10.0

Structured OWASP Top 10 (2021) pass over BODP's actual attack surface,
performed as Phase 10.0 of MASTER_PLAN.md's hardening phase. Per the
phase's own scope decision, this is a verification pass over
already-real security work, not a rebuild — most findings below are
"Confirmed Safe," with two real findings (marked **FIXED**) that this
pass surfaced and corrected on the spot, per the phase's stated policy
of treating a real finding as an unplanned fix rather than deferring it.

Every finding below was checked directly against the running code or a
live test against the dev stack — not inferred from reading alone.

| OWASP Category | Area | Status | Evidence |
|---|---|---|---|
| A01 Broken Access Control | Every `admin_*.py` router's routes | Confirmed Safe | Every real route (excluding private `_to_*` helper functions, which aren't endpoints) across all 20 `admin_*.py` routers gates via `require_permission(...)`, `require_admin`, or `require_any_permission(...)`. Two apparent gaps (`admin_boundary.py`'s `list_boundaries`/`get_default_boundary`, `admin_settings.py`'s two visualization-settings GET routes) are deliberate, documented public reads (consumed by the public `/visualize` page); their mutating counterparts are correctly gated. |
| A01 | `requests.py::download_extraction` | Confirmed Safe | Line 179 checks `grant.status == GrantStatus.ACTIVE.value` before issuing a presigned download URL — an expired/revoked grant cannot download. |
| A01 | `admin_requests.py::download_supporting_document` | Confirmed Safe | Gated by `require_permission`, not just authentication. |
| A02 Cryptographic Failures | Password hashing | Confirmed Safe | `core/security.py` uses real bcrypt (`bcrypt.hashpw`/`bcrypt.checkpw`) with configurable rounds via `BCRYPT_ROUNDS`, not a raw hash. |
| A02 | JWT signing | Confirmed Safe | HS256 via `python-jose`, `JWT_SECRET_KEY` has a documented "must be overridden in staging/production" default. |
| A02 | Presigned URL expiry | Confirmed Safe | Every `presign_get`/`presign_put` call site (`admin_reports_service.py:74`, `requests.py:193`, `admin_requests.py:108`) passes an explicit `expires_in_seconds` sourced from a real config setting, never unbounded. |
| A02 | Presigned URL scope-escalation | **Verified live, Confirmed Safe** | Generated a real presigned GET URL for one MinIO object, hand-edited the URL's key segment to point at a different object while reusing the original signature, and made the actual HTTP request against the live dev MinIO. Result: `403 SignatureDoesNotMatch` — boto3's SigV4 signing binds the signature to the exact key, confirmed empirically, not just by reading the library's docs. |
| A03 Injection | SQL construction across `backend/app/services/*.py` | Confirmed Safe | Every SQLAlchemy Core/ORM query uses parameter binding. The one file using raw f-string SQL (`tabular_query_service.py`, DuckDB-backed Parquet querying) was traced call-site by call-site: every genuine user-supplied *value* (parameters, dates, quality flags, stations) goes through `?` placeholder binding; f-string interpolation is used only for SQL *structure* — column names checked against a fixed internal set, `date_trunc` units chosen from a fixed 3-value dict (`_resolution_trunc`), and internal file paths derived from server-controlled `DatasetFile.storage_key` (itself sanitized at upload time). No user-controlled string ever reaches a query via string interpolation. |
| A04 Insecure Design / SSRF | URL-shaped client-supplied fields (`photo_url`, `featured_image_url`) | Confirmed Safe | Grepped every service that writes these fields (`admin_about_team_service.py`, `admin_blog_service.py`, `admin_cms_service.py`) for `httpx`/`requests`/`urlopen` — zero hits. These URLs are stored and rendered client-side as plain `<img>` sources only; the backend never fetches them. |
| A04 | Webhook/callback-URL fields | N/A | Confirmed via repo-wide grep: no such fields exist anywhere in the schema. |
| A05 Security Misconfiguration | API docs in production | Confirmed Safe | `main.py:54-56` sets `docs_url`/`redoc_url`/`openapi_url` to `None` when `settings.is_production`, which correctly resolves to `True` given `docker-compose.prod.yml`'s `ENVIRONMENT: production`. |
| A05 | CORS | Confirmed Safe | `allow_origins=settings.CORS_ORIGINS` reads a real, env-configurable domain list (`.env.prod.example` sets a real HTTPS domain), never a wildcard. |
| A05 | DEBUG in production | Confirmed Safe | `docker-compose.prod.yml` explicitly sets `DEBUG: "false"`. |
| A07 Identification/Auth Failures | Failed-login lockout | **REAL BUG FOUND AND FIXED** | See "Fix: Failed-login lockout was unreachable" below. |
| A07 | Upload path traversal | Confirmed Safe | `sanitize_filename()` (`storage/keys.py`) tested live against 4 crafted inputs (Unix `../../etc/passwd`, Windows-style backslash traversal, mixed `a/b/../../c.csv`, and a deep `../../../root/.ssh/id_rsa`) — every one collapses to a safe basename with zero surviving path separators or `..` segments. |
| A08 Software/Data Integrity | Magic-byte content sniffing | **REAL BUG FOUND AND FIXED** | See "Fix: CSV magic-byte sniff didn't catch arbitrary binaries" below. Checksum-on-read enforcement (verified computed-but-never-checked at retrieval) is tracked as Phase 10.5's own scope, not re-litigated here. |

## Fix: Failed-login lockout was unreachable

**Severity: real, significant.** `auth_service.py::authenticate()` checked
`_is_locked_out()` *after* the wrong-password branch's `raise AuthError(...)`
— meaning the lockout check was structurally unreachable from the exact
attack scenario it exists to defend against (an attacker repeatedly
guessing wrong passwords). Live-tested against the running dev stack: 6
consecutive wrong-password attempts against a fresh test user all
returned `401`, never `429`, confirming the control was completely
non-functional for its primary purpose.

The existing test (`test_login_lockout_after_repeated_failures`) only
verified lockout via a 6th attempt using the *correct* password — which
happened to still reach the (misplaced) lockout check, since a
successful `verify_password` falls through the wrong-password branch
entirely. This let a real bug ship with what looked like passing test
coverage.

**Fix** (`backend/app/services/auth_service.py`): moved the
`_is_locked_out()` check to the top of `authenticate()`, before any
password verification. This also closes a second implicit gap the old
ordering had — a lucky correct-password guess during an active lockout
window would previously have bypassed the lockout entirely.

**New test added**
(`test_login_lockout_blocks_continued_wrong_password_attempts`,
`backend/tests/test_auth.py`) exercises the actual attack scenario: 5
wrong-password attempts, a 6th wrong-password attempt (must be `429`,
not `401`), and a 7th attempt with the *correct* password (must stay
`429` — the window doesn't clear on a correct guess). Full `test_auth.py`
suite re-run: 23/23 passed after the fix, no regressions to
suspended-account or unverified-email flows.

## Fix: CSV magic-byte sniff didn't catch arbitrary binaries

**Severity: real, moderate — not an ingestion bypass, but a design-intent
gap.** `parsers/registry.py::sniff_format()`'s `.csv` branch only
rejected content matching HDF5/NetCDF/TIFF magic bytes (the specific
other scientific formats this registry recognizes). A CSV-named upload
containing an arbitrary binary with no such signature — e.g. a renamed
Windows executable (`MZ` header) or Linux binary (`\x7fELF` header) —
passed this cheap pre-check entirely.

Live-tested: a fake `.csv` with a PE header was **accepted** by
`validate_content_matches_extension()` before the fix. The file was
still ultimately rejected by `CsvParser.parse()` (`pd.read_csv` fails to
UTF-8-decode binary bytes) — so this was never a real ingestion bypass —
but it violated the function's own documented intent ("reject before any
parser attempts real work") and meant a malformed upload paid the cost
of a full download + parse attempt before failing, instead of failing at
the cheap header-sniff stage.

**Fix** (`backend/app/services/parsers/registry.py`): added a
two-part check for `.csv` — the header must decode as valid UTF-8, *and*
must not contain a NUL byte. Decode-validity alone was insufficient and
confirmed so by testing: `\x7fELF\x00...` decodes as valid UTF-8 (both
`\x7f` and `\x00` are valid single-byte UTF-8 codepoints), so the NUL-byte
check was added specifically to catch it — a real CSV can never
legitimately contain an embedded NUL.

Verified against 6 real cases: 2 disguised binaries (PE, ELF) correctly
rejected; 4 legitimate CSV variants (plain ASCII, UTF-8 BOM from Excel
exports, genuine non-ASCII UTF-8 content — Bengali station names,
directly relevant to this project's real data — and legacy Latin-1
encoding) all still correctly accepted, confirming no false-positive
regression on real-world CSV encodings this project actually ingests.

**New tests added** (`backend/tests/test_parsers.py`):
`test_rejects_arbitrary_binary_disguised_as_csv` and
`test_accepts_legitimate_non_ascii_csv_encodings`. Full `test_parsers.py`
suite re-run: 51/51 passed after the fix.

## Not yet covered by this pass (tracked in later Phase 10 sub-phases)

- **A08 checksum-on-read enforcement** — `verify_checksum()` exists but
  is never called on file retrieval; Phase 10.5's own scope.
- **Secrets rotation process** — documented separately in
  `docs/VPS_PROVISIONING.md`'s secrets-configuration section (Phase 10.7).

## Phase 10.1 — CSP header

Added to `nginx/conf.d/bodp.conf` alongside the existing HSTS/
X-Content-Type-Options/X-Frame-Options/Referrer-Policy headers (Nginx is
already the single place all response headers are added, for both `/api/`
and `/`):

```
default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'
'unsafe-inline'; img-src 'self' data: blob: https://storage.bodp.example.org;
connect-src 'self'; font-src 'self' data:; frame-ancestors 'none';
base-uri 'self'; form-action 'self'
```

**Confirmed the app's actual asset footprint before writing the policy**,
not guessed: the live frontend's rendered HTML has zero external
`src`/`href` references (everything same-origin `/`-relative); Plotly.js
is npm-bundled (`package.json`), not loaded from a CDN; no Google Fonts
or other external font/CDN reference exists anywhere in `layout.tsx`/
`globals.css`; the one real external-domain need is `img-src`, since
avatars/media resolve through `NEXT_PUBLIC_STORAGE_PUBLIC_URL`
(`frontend/src/lib/avatar.ts`) to the object-storage public endpoint —
confirmed by tracing `avatarUrl()`'s actual construction, not assumed.

**`'unsafe-inline'` on `script-src`/`style-src` is a deliberate,
documented trade-off, not an oversight**: Next.js's built-in hydration/
style injection and Plotly's canvas/WebGL render path (inline style tags
for chart layout) aren't nonce-compatible without per-request nonce
plumbing through a `middleware.ts`, which doesn't exist in this app
today. Revisit if/when that's built.

**`storage.bodp.example.org` is a placeholder**, matching the same
`bodp.example.org` convention the rest of `bodp.conf` already uses —
replace with `NEXT_PUBLIC_STORAGE_PUBLIC_URL`'s real deployed domain at
the same time the main domain placeholder is replaced during VPS setup.
Flagging this as a genuine current gap: neither `.env.prod.example` nor
`docs/VPS_PROVISIONING.md` documents what this value should actually be
in production today — tracked for Phase 10.7 (documentation).

No report-collection endpoint exists in this app, so the header ships
enforcing directly rather than report-only first.

**Verified**: real `nginx -t` syntax validation (throwaway container,
self-signed cert, mounted on the actual dev Docker network so the
`backend`/`frontend` upstreams resolved) — `nginx: the configuration
file /etc/nginx/nginx.conf syntax is ok`. The only failures encountered
were unrelated to this change (missing `limit_req_zone` definitions,
which live in the base `nginx.conf` this isolated test didn't mount —
not a real defect). Full live-request testing (loading every page with
devtools open, checking for console CSP violations) requires a real
TLS-terminated deployment and is deferred to a pre-launch manual QA pass
alongside Phase 10.8's frontend cross-check, since this dev environment
has no Nginx/TLS layer in front of it at all (dev serves frontend/backend
directly on separate ports).

## Phase 10.2 — Rate limiting: a foundational bug found mid-implementation

While closing the originally-scoped gap (adding `RATE_LIMIT_MUTATIONS =
"30/minute"` to `requests.py`'s request-submission/extraction-creation
routes and `visualize.py`'s 5 `bounded_to_thread`-gated endpoints — both
confirmed correct via live testing, first 30 requests succeed then 429
from request 31 onward), a **significant, previously-undetected bug**
surfaced: `limiter.py`'s `default_limits=[settings.RATE_LIMIT_DEFAULT]`
(the intended "120/min global floor" for every undecorated route) has
**never actually been enforced, for any route, in this application**.

**Root cause**: `SlowAPIMiddleware.dispatch()` resolves the current
route's handler via `slowapi.middleware._find_route_handler()`, which
loops `app.routes` checking `hasattr(route, "endpoint")`. In this
project's installed FastAPI version (0.141.1), every router mounted via
`include_router()` — i.e. every route in the entire app except the 4
literal top-level ones (`/api/docs`, `/api/openapi.json`, etc.) — appears
in `app.routes` as a `fastapi.routing._IncludedRouter` object, which has
no `.endpoint` attribute at all (confirmed directly:
`hasattr(_IncludedRouter_instance, "endpoint")` is `False`). Since
`_find_route_handler` never finds a matching route with the attribute it
checks for, it returns `None` for literally every mounted route.
`_should_exempt()` then short-circuits on `if handler is None: return
True` — silently exempting the request from any middleware-level check,
`default_limits` included. `slowapi` 0.1.10 is the latest available
version (confirmed via `pip index versions`); there is no newer release
that fixes this — it's a genuine incompatibility with this FastAPI
version's newer internal routing representation, not something to
upgrade past.

**Real impact confirmed live**: 150 rapid unauthenticated requests
against `/catalog/search` (public, no auth) all returned `200` — zero
rate limiting whatsoever, despite `RATE_LIMIT_DEFAULT = "120/minute"`
appearing to configure exactly this. Routes with an explicit
`@limiter.limit(...)` decorator (auth, content, and this phase's own new
mutation-route decorators) are **not** affected — decorator-based
limiting is checked via a different code path (through the endpoint's
own dependency injection, `in_middleware=False`), confirmed still
working correctly by this phase's own live tests above. The break is
specifically the middleware-only "global floor" for routes with no
explicit decorator.

**Scope of the fix, this pass**: `catalog.py` was fixed — the
highest-risk file (public, unauthenticated, 6 routes) — by adding
explicit `@limiter.limit(settings.RATE_LIMIT_DEFAULT)` decorators to
every route, which bypasses the broken middleware path entirely (the
same mechanism that already correctly protects auth/content/this
phase's mutation routes). Verified live: the identical 150-request test
against `/catalog/search` now returns 120×`200` then 30×`429`, matching
the configured limit exactly; all 6 catalog routes smoke-tested
individually with no regression.

**Deliberately NOT fixed in this pass**: 20 further router files
(`me.py` and 19 `admin_*.py` files) have zero rate-limit decorators and
remain exposed to the same middleware bug. Lower priority than
`catalog.py` since every one of these routes already requires
authentication (`me.py`) or authentication + a specific permission
(every `admin_*.py` route, confirmed in Phase 10.0's access-control
pass) — the practical abuse surface is a logged-in user/admin hammering
an endpoint, not an anonymous internet-wide scraper. This is real,
tracked technical debt, not resolved — flagged explicitly here rather
than silently left off the list. A systemic fix (either decorating every
remaining route, or replacing `_find_route_handler`'s approach — e.g.
resolving the handler via `request.scope["route"]`, which FastAPI/
Starlette populates correctly during real request routing regardless of
`_IncludedRouter` wrapping, unlike the ad-hoc `route.matches()` re-walk
`slowapi`'s middleware does today) should be scheduled as a followup,
scoped and prioritized by the person who owns this codebase going
forward.

## Changelog

- 2026-08-21 — Initial Phase 10.0 audit pass. Two real findings fixed on
  the spot (failed-login lockout ordering, CSV magic-byte sniff gap),
  both with new regression test coverage. All other checklist items
  confirmed safe via direct code inspection and/or live testing against
  the dev stack.
- 2026-08-21 — Phase 10.1: added the CSP header, verified via real
  `nginx -t` syntax validation. One documentation gap flagged (the
  production storage public-endpoint domain isn't documented anywhere
  yet) for Phase 10.7 to close.
- 2026-08-21 — Phase 10.2: added `RATE_LIMIT_MUTATIONS` to
  `requests.py`/`visualize.py`'s heavy endpoints as scoped. Found and
  fixed a significant pre-existing bug in the process: slowapi's
  `default_limits` global floor has never worked for any undecorated
  route due to a FastAPI-version routing incompatibility. Fixed the
  highest-risk case (`catalog.py`, public/unauthenticated) with explicit
  decorators; 20 further authenticated/permission-gated router files
  remain affected and are tracked as real, explicit technical debt, not
  silently resolved.
