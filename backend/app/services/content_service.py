import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import AboutTeamMember, BlogPost, BlogPostStatus
from app.services import admin_about_team_service
from app.services import cms_service


async def get_public_cms_blocks(db: AsyncSession, page: str) -> list[dict]:
    return await cms_service.get_blocks(db, page, active_only=True)


async def list_team_members(db: AsyncSession) -> list[AboutTeamMember]:
    return await admin_about_team_service.list_members(db)


async def list_published_posts(
    db: AsyncSession, *, category: str | None = None, tag_key: str | None = None
) -> list[BlogPost]:
    query = (
        select(BlogPost)
        .options(selectinload(BlogPost.featured_image))
        .where(BlogPost.status == BlogPostStatus.PUBLISHED.value)
        .order_by(BlogPost.created_at.desc())
    )
    if category:
        query = query.where(BlogPost.category == category)
    if tag_key:
        query = query.where(BlogPost.tag_key == tag_key)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_published_post(db: AsyncSession, post_id: uuid.UUID) -> BlogPost:
    result = await db.execute(
        select(BlogPost)
        .options(selectinload(BlogPost.featured_image))
        .where(BlogPost.id == post_id, BlogPost.status == BlogPostStatus.PUBLISHED.value)
    )
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    post.views = (post.views or 0) + 1
    await db.commit()
    await db.refresh(post, attribute_names=["views"])
    return post
