import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sanitize import sanitize_html
from app.models.admin import CmsBlock
from app.models.audit import AuditActionType
from app.models.user import User
from app.schemas.admin_cms import CmsBlockCreate, CmsBlockUpdate
from app.services import cms_service
from app.services.audit_service import write_audit_log


async def list_blocks(db: AsyncSession, *, page: str | None = None) -> list[CmsBlock]:
    query = select(CmsBlock).order_by(CmsBlock.page, CmsBlock.display_order, CmsBlock.key)
    if page:
        query = query.where(CmsBlock.page == page)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_block(db: AsyncSession, block_id: uuid.UUID) -> CmsBlock:
    block = await db.get(CmsBlock, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail="CMS block not found")
    return block


async def create_block(
    db: AsyncSession, *, payload: CmsBlockCreate, actor: User, ip_address: str | None
) -> CmsBlock:
    block = CmsBlock(
        key=payload.key,
        page=payload.page,
        section=payload.section,
        label=payload.label,
        value=sanitize_html(payload.value),
        display_order=payload.display_order,
        is_system_block=False,  # admin-created blocks are always Custom Blocks
        is_active=payload.is_active,
    )
    db.add(block)
    try:
        await db.flush()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A block with this key already exists") from exc

    await write_audit_log(
        db,
        actor=actor,
        action="Created CMS block",
        action_type=AuditActionType.CONTENT,
        target=f"{block.page}/{block.key}",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(block)
    await cms_service.invalidate_page_cache(block.page)
    return block


async def update_block(
    db: AsyncSession, *, block: CmsBlock, payload: CmsBlockUpdate, actor: User, ip_address: str | None
) -> CmsBlock:
    updates = payload.model_dump(exclude_unset=True)
    if "value" in updates:
        updates["value"] = sanitize_html(updates["value"])
    for field, value in updates.items():
        setattr(block, field, value)

    await write_audit_log(
        db,
        actor=actor,
        action="Updated CMS block",
        action_type=AuditActionType.CONTENT,
        target=f"{block.page}/{block.key}",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(block)
    await cms_service.invalidate_page_cache(block.page)
    return block


async def delete_block(db: AsyncSession, *, block: CmsBlock, actor: User, ip_address: str | None) -> None:
    if block.is_system_block:
        raise HTTPException(status_code=403, detail="System blocks cannot be deleted")

    page = block.page
    await write_audit_log(
        db,
        actor=actor,
        action="Deleted CMS block",
        action_type=AuditActionType.CONTENT,
        target=f"{block.page}/{block.key}",
        ip_address=ip_address,
    )
    await db.delete(block)
    await db.commit()
    await cms_service.invalidate_page_cache(page)
