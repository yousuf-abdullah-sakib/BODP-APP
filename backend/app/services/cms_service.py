from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get_json, cache_set_json
from app.models.admin import CmsBlock

_CACHE_TTL_SECONDS = 300


async def get_blocks(db: AsyncSession, page: str) -> list[dict]:
    cache_key = f"cms:blocks:{page}"
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    result = await db.execute(select(CmsBlock).where(CmsBlock.page == page).order_by(CmsBlock.key))
    blocks = [
        {"key": b.key, "page": b.page, "label": b.label, "value": b.value}
        for b in result.scalars().all()
    ]
    await cache_set_json(cache_key, blocks, ttl_seconds=_CACHE_TTL_SECONDS)
    return blocks
