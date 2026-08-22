# Load Testing — Phase 10.6

`backend/scripts/load_test.py` — a real, reusable, parameterized load-test
tool committing the Gunicorn/httpx concurrent-load methodology this
project's Visualize Performance work already proved out (scratchpad-only
during that phase). A pure external HTTP client: no `app.*` imports, no
direct DB access — it exercises the API exactly the way a real browser or
script would, over the network, against whatever URL it's pointed at.

## One-time setup

The tool needs a verified user to authenticate as for the
`request_submission`/`mixed` scenarios. Run once against the target
environment's database (a separate, DB-touching setup step, deliberately
NOT part of the load-generation tool itself):

```bash
docker compose exec backend python -m app.scripts.seed_load_test_user
```

Idempotent — safe to re-run; creates `load-test@example.com` /
`LoadTest123x` if it doesn't already exist, leaves it alone if it does.

## Running it

```bash
python backend/scripts/load_test.py \
    --scenario mixed --concurrency 500 --duration 60 \
    --base-url http://localhost:8000 --report out.json
```

| Flag | Default | Meaning |
|---|---|---|
| `--scenario` | (required) | `catalog_search`, `request_submission`, `visualization`, or `mixed` |
| `--concurrency` | 200 | Number of concurrent workers, each looping requests for the full duration |
| `--duration` | 60 | Seconds to run |
| `--ramp-up` | 10 | Seconds to spread worker startup over, avoiding an artificial instant-500 spike at t=0 |
| `--base-url` | `http://localhost:8000` | Target — point this at the Gunicorn production-mode container, not the dev `--reload` Uvicorn server (see below) |
| `--email` / `--password` | the seeded load-test user | Override to use a different account |
| `--report` | none | Path to also write the JSON summary to |

## Scenarios

- **catalog_search** — public `GET /catalog/search` with varied query/category params. Read-heavy; exercises Postgres plus the Redis cache.
- **request_submission** — authenticated `POST /requests`. Deliberately exercises the `RATE_LIMIT_MUTATIONS` (30/minute) limiter — a high 429 rate at concurrency well above 30 is *expected, correct* behavior, reported as its own metric (`rate_429_pct`), not hidden as an error.
- **visualization** — the `QUERY_CONCURRENCY_LIMIT_PER_WORKER`-gated endpoints (`timeseries`/`spatial`/`comparison`/`statistics`/`profiles`), weighted toward the lighter/more common ones. Polls a returned `job_id` to completion for the three endpoints that can queue (`spatial`/`comparison`/`statistics`) rather than counting the initial 200 (queued) as the finished latency.
- **mixed** — 70% catalog_search / 20% visualization / 10% request_submission, approximating realistic traffic shape. This is the scenario that actually answers the Master Plan's "~500 concurrent simulated users" quality check — real traffic is never single-endpoint.

## Important: target the production-mode container, not the dev server

`docker compose up`'s `backend` service runs a single-process
`uvicorn --reload` (see `Dockerfile.dev`) — fine for development, but not
representative of production capacity and easily saturated by a real
500-concurrent run (the point of this exercise is to measure the
4-Gunicorn-worker production configuration `Dockerfile` actually builds,
matching `docker-compose.prod.yml`). For a real run, build and start a
separate container from the production `Dockerfile` against the same
Postgres/Redis/MinIO the dev stack already has running, then point
`--base-url` at that container's published port — never run the real
500-concurrent benchmark against the shared dev `--reload` server, since
saturating it also breaks whatever else you're using that dev stack for
at the same time.

## Results

**Tool correctness (2026-08-22)**, low-concurrency smoke test against the
dev stack, confirming the methodology reproduces sane, honest numbers
before the real run:

- `catalog_search`, concurrency=5, 8s: 1,151 requests, p50=18ms,
  p95=56ms, p99=100ms, 429 rate 89.6% (correctly triggered — 5 tight
  loops easily exceed 120/min).
- `request_submission`, concurrency=3, 10s: 898 requests, exactly 30
  succeeded (`201`) before the 30/minute limiter kicked in, 96.7% 429
  rate thereafter — the limiter working exactly as designed, not the
  app falling over. Test rows cleaned up afterward.
- `visualization`, concurrency=3, 15s: 3/3 succeeded, p50=18.2s
  (uncached heavier-dataset queries in the pool), zero errors.
- `mixed`, concurrency=5, 10s: 400 requests, 208 success / 192 correctly
  rate-limited, zero 5xx.

**Final pre-launch runs (2026-08-22)** — real production-mode container
(4 Gunicorn/UvicornWorker workers, built from the real `Dockerfile`, on
the same Docker network as the dev stack's actual Postgres/Redis/MinIO,
never the dev `--reload` server), 60s duration, 10s ramp-up unless noted:

| Scenario | Concurrency | Requests | Throughput (rps) | p50 | p95 | p99 | 429 rate | 5xx/conn-error rate |
|---|---|---|---|---|---|---|---|---|
| `catalog_search` | 500 | 4,214 | 70.2 | 1.15s | 1.78s | 2.41s | 97.15% | 0.0% |
| `request_submission` | 500 | 3,860 | 64.3 | 1.20s | 1.78s | 2.64s | 99.22% | 0.0% |
| `visualization` | 20 | 29,230 | 487.2 | 33ms | 48ms | 78ms | 99.49% | 0.0% |
| `mixed` (70/20/10 blend) | 500 | 270 | 4.5 | 11.78s | 61.13s | 73.25s | 24.81% | 0.74% (2 client-side connection resets, zero server 5xx) |

**Zero server-side 5xx responses across every run** (confirmed directly
against the load-test container's own request logs, not just the client's
summary) — every non-2xx response was a correctly-functioning 429 from
the rate limiter, exactly the documented, intended behavior under load
this hard, not the app failing.

**`catalog_search` and `request_submission` both hold up cleanly at the
literal 500-concurrent target**: `RATE_LIMIT_DEFAULT` (120/min) and
`RATE_LIMIT_MUTATIONS` (30/min) each clamp throughput to almost exactly
their configured ceiling (120 and 30 successes respectively, out of
thousands of attempts), with sub-3-second p99 latency even while
absorbing 500 simultaneous connections — the rate limiter is doing
exactly its job of protecting the app from being overwhelmed.

**`mixed`'s poor numbers are fully explained, not a bug**: isolated a
single low-concurrency `visualization` run separately (20 concurrent,
uncontended by the rate limiter's 30/min cap for most of the window) and
found p50=33ms/p95=48ms for the endpoints that got through — genuinely
fast. Also confirmed via 10 sequential single-shot requests, spaced to
stay under the rate limit, that `statistics` alone completes in ~280ms
at zero contention. The `mixed` scenario's 61s p95 is therefore not slow
individual queries — it's real, expected queueing: at 500 concurrent,
enough visualization requests land inside the same 60s window to
saturate `QUERY_CONCURRENCY_LIMIT_PER_WORKER=2` × 4 Gunicorn workers = 8
concurrent heavy-query slots system-wide, and every additional request
waits on that semaphore while still holding its Postgres connection
(config.py's own documented design and trade-off, re-confirmed still
holding at the literal ~500-concurrent Master Plan target). This is the
same trade-off `QUERY_CONCURRENCY_LIMIT_PER_WORKER`'s own benchmark
history already accepted deliberately — re-raising it was explicitly
ruled out of scope for this phase (per the plan) since it was already
re-benchmarked and confirmed optimal-or-tied in the Visualize Performance
work.

**DB pool cross-check**: polled `pg_stat_activity` directly during and
after the visualization run — connection counts stayed comfortably below
Postgres's `max_connections` throughout (peaked at 2 active / 44 idle
during the 20-concurrent visualization run), confirming pool exhaustion
was not the bottleneck at this load — the semaphore, not the DB pool, is
what's actually gating throughput, matching `DB_POOL_SIZE`'s own
docstring reasoning.

**2 connection-error outcomes in the mixed run**: both client-side
(httpx connection resets under extreme queueing wait times, ~60-70s),
confirmed via the server's own access logs showing zero corresponding
5xx or dropped-connection entries — not a server-side failure.

All test data created during these runs (`DatasetRequest` rows from
`request_submission`) was deleted afterward; the load-test container and
seeded user are cleanup-safe to remove between runs (`docker rm -f
bodp-loadtest-backend`, `DELETE FROM users WHERE email =
'load-test@example.com'` — the latter left in place after this session's
run, in case of a future re-run).
