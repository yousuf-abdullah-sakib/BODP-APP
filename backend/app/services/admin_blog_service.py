import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.sanitize import sanitize_html
from app.models.admin import BlogPost, BlogPostStatus
from app.models.audit import AuditActionType
from app.models.user import User
from app.schemas.admin_blog import BlogPostCreate, BlogPostUpdate
from app.services.audit_service import write_audit_log


async def list_posts(
    db: AsyncSession, *, search: str | None = None, status_filter: str | None = None
) -> list[BlogPost]:
    query = select(BlogPost).options(selectinload(BlogPost.featured_image)).order_by(BlogPost.created_at.desc())
    if search:
        query = query.where(BlogPost.title.ilike(f"%{search}%"))
    if status_filter:
        query = query.where(BlogPost.status == status_filter)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_post(db: AsyncSession, post_id: uuid.UUID) -> BlogPost:
    result = await db.execute(
        select(BlogPost).options(selectinload(BlogPost.featured_image)).where(BlogPost.id == post_id)
    )
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Blog post not found")
    return post


async def create_post(
    db: AsyncSession, *, payload: BlogPostCreate, actor: User, ip_address: str | None
) -> BlogPost:
    post = BlogPost(
        title=payload.title,
        category=payload.category,
        tag_key=payload.tag_key,
        author_id=actor.id,
        author_name=payload.author_name or actor.full_name,
        excerpt=payload.excerpt,
        content_html=sanitize_html(payload.content_html),
        status=payload.status,
        featured=payload.featured,
        featured_image_id=payload.featured_image_id,
        tags=payload.tags,
    )
    db.add(post)
    await write_audit_log(
        db,
        actor=actor,
        action=f"Created blog post ({post.status})",
        action_type=AuditActionType.CONTENT,
        target=post.title,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_post(db, post.id)


async def update_post(
    db: AsyncSession, *, post: BlogPost, payload: BlogPostUpdate, actor: User, ip_address: str | None
) -> BlogPost:
    updates = payload.model_dump(exclude_unset=True)
    if "content_html" in updates:
        updates["content_html"] = sanitize_html(updates["content_html"])
    for field, value in updates.items():
        setattr(post, field, value)

    await write_audit_log(
        db,
        actor=actor,
        action="Updated blog post",
        action_type=AuditActionType.CONTENT,
        target=post.title,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_post(db, post.id)


async def set_status(
    db: AsyncSession, *, post: BlogPost, published: bool, actor: User, ip_address: str | None
) -> BlogPost:
    post.status = BlogPostStatus.PUBLISHED.value if published else BlogPostStatus.DRAFT.value
    await write_audit_log(
        db,
        actor=actor,
        action=f"{'Published' if published else 'Unpublished'} blog post",
        action_type=AuditActionType.CONTENT,
        target=post.title,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_post(db, post.id)


async def delete_post(db: AsyncSession, *, post: BlogPost, actor: User, ip_address: str | None) -> None:
    await write_audit_log(
        db,
        actor=actor,
        action="Deleted blog post",
        action_type=AuditActionType.CONTENT,
        target=post.title,
        ip_address=ip_address,
    )
    await db.delete(post)
    await db.commit()
