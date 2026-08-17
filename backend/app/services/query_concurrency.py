"""PLAN.md Phase 5 — bounds how many DuckDB (Parquet)/xarray (Zarr)
queries run concurrently inside one worker process.

Every tabular_query_service/gridded_query_service call is synchronous
(DuckDB and xarray/s3fs have no native asyncio API) and is dispatched via
asyncio.to_thread from catalog_service.py/visualize_service.py/
admin_qc_service.py. Each one is genuinely CPU-bound work, not I/O wait
— a real production-mode (4 Gunicorn workers) concurrency benchmark
against this project's real MinIO/Postgres measured the backend
container's CPU hitting 1000%+ (of the host's 12 cores) under 50-500
concurrent /catalog/{id}/records requests, with DuckDB already capped at
2 threads per connection (tabular_query_service._DUCKDB_THREADS_PER_
CONNECTION). Unbounded asyncio.to_thread concurrency meant far more
simultaneous CPU-bound queries than the machine could actually run in
parallel, which had a second, compounding effect: each request holds its
FastAPI-injected AsyncSession (a pooled Postgres connection) open for the
ENTIRE call, including the time spent waiting on this now-oversubscribed
CPU work — so the Postgres connection pool (pool_size=20 + max_overflow=
10 per worker) was exhausted by requests parked waiting for CPU, not by
genuine Postgres load (the same benchmark showed Postgres itself only
reached ~67% CPU while the pool was throwing "QueuePool limit... timed
out" errors).

bounded_to_thread is a drop-in replacement for asyncio.to_thread that
acquires a per-process semaphore first — a request that can't get a slot
waits ON THE SEMAPHORE (still holding its Postgres connection, same as
before), but the number of THREADS actually doing CPU work at once is
now bounded to something the host can genuinely execute in parallel,
instead of every concurrent request spawning its own thread immediately
regardless of how many are already running. This does not eliminate
connection-pool pressure under extreme overload — it bounds the CPU
contention that was making every individual query far slower than
necessary under load, which is what was driving connections to be held
for so long in the first place.
"""

import asyncio
from typing import ParamSpec, TypeVar

from app.core.config import settings

P = ParamSpec("P")
T = TypeVar("T")

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    # Lazily created on first use, INSIDE the worker process's own event
    # loop — a module-level asyncio.Semaphore() created at import time
    # would bind to whatever event loop happens to exist at import
    # (often none yet, under Gunicorn's pre-fork model), which can raise
    # or silently misbehave depending on asyncio version. Deliberately
    # NOT shared across Gunicorn worker processes (each has its own
    # Python interpreter/memory) — the effective system-wide limit is
    # (worker count x QUERY_CONCURRENCY_LIMIT_PER_WORKER), matching how
    # the DB connection pool is already configured per-process, not
    # globally.
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.QUERY_CONCURRENCY_LIMIT_PER_WORKER)
    return _semaphore


async def bounded_to_thread(func, /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Drop-in replacement for asyncio.to_thread(func, *args, **kwargs)
    that limits how many such calls run concurrently within this worker
    process — see module docstring for why."""
    async with _get_semaphore():
        return await asyncio.to_thread(func, *args, **kwargs)
