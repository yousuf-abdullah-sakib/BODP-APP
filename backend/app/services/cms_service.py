from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get_json, cache_set_json
from app.models.admin import CmsBlock

_CACHE_TTL_SECONDS = 300


def cache_key_for(page: str) -> str:
    return f"cms:blocks:{page}"


async def invalidate_page_cache(page: str) -> None:
    await cache_delete(cache_key_for(page))


async def get_blocks(db: AsyncSession, page: str, *, active_only: bool = True) -> list[dict]:
    """Cached read of a page's CMS blocks. Every admin write path
    (create/update/delete in admin_cms_service.py) invalidates this page's
    cache key immediately — the quality check ("editing a CMS block
    updates the public page on next load") depends on this never relying
    on the TTL to expire naturally."""
    cache_key = cache_key_for(page)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        blocks = cached
    else:
        result = await db.execute(
            select(CmsBlock).where(CmsBlock.page == page).order_by(CmsBlock.display_order, CmsBlock.key)
        )
        blocks = [
            {
                "id": str(b.id),
                "key": b.key,
                "page": b.page,
                "section": b.section,
                "label": b.label,
                "value": b.value,
                "display_order": b.display_order,
                "is_system_block": b.is_system_block,
                "is_active": b.is_active,
            }
            for b in result.scalars().all()
        ]
        await cache_set_json(cache_key, blocks, ttl_seconds=_CACHE_TTL_SECONDS)

    if active_only:
        return [b for b in blocks if b["is_active"]]
    return blocks
