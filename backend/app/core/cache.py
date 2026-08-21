import asyncio
import json
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Keyed by the running event loop (via id(), not the loop object itself,
# so a garbage-collected loop's key doesn't keep the loop alive) rather
# than a single module-level singleton — redis.asyncio.Redis's underlying
# connections are permanently bound to whichever loop created them, same
# constraint asyncpg has (see worker/tasks/bulk_import.py's
# _run_transfer_with_fresh_engine docstring for the fuller explanation of
# this class of bug). A single shared client broke for real once a second
# task (Dataset Default-View Snapshot's asyncio.run()-in-a-thread
# generator) called cache_get_json/cache_set_json from its own isolated
# loop: the client, first bound to the main app/test loop, then got used
# from a second loop and corrupted itself for every future caller on the
# first loop too ("Event loop is closed"). One client per loop fixes this
# at the root rather than working around it in each caller.
_redis_clients: dict[int, aioredis.Redis] = {}


def get_redis() -> aioredis.Redis:
    """Async Redis client for caching (distinct from Celery's own broker/
    result-backend connections), one per running event loop. Catalog
    filter-option lists and popular searches are cached here (Master Plan
    §3 Phase 3 task 6) since they change rarely but are hit on nearly
    every catalog page load."""
    loop_id = id(asyncio.get_running_loop())
    client = _redis_clients.get(loop_id)
    if client is None:
        client = aioredis.from_url(str(settings.REDIS_URL), decode_responses=True)
        _redis_clients[loop_id] = client
    return client


async def cache_get_json(key: str) -> Any | None:
    try:
        raw = await get_redis().get(key)
    except Exception:
        logger.warning("cache.get_failed", key=key, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def cache_set_json(key: str, value: Any, *, ttl_seconds: int) -> None:
    try:
        await get_redis().set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        # Cache is a performance optimization, not a correctness dependency —
        # a Redis outage should degrade to "always compute fresh", never 500.
        logger.warning("cache.set_failed", key=key, exc_info=True)


async def cache_delete(*keys: str) -> None:
    if not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception:
        logger.warning("cache.delete_failed", keys=keys, exc_info=True)
