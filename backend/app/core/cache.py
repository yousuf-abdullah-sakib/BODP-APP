import json
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_redis_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Shared async Redis client for caching (distinct from Celery's own
    broker/result-backend connections). Catalog filter-option lists and
    popular searches are cached here (Master Plan §3 Phase 3 task 6) since
    they change rarely but are hit on nearly every catalog page load."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(str(settings.REDIS_URL), decode_responses=True)
    return _redis_client


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
