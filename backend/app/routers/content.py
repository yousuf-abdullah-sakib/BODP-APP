import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.content import (
    PublicBlogPostDetail,
    PublicBlogPostSummary,
    PublicCmsBlock,
    PublicTeamMember,
)
from app.services import content_service
from app.services.admin_media_service import media_url

router = APIRouter(prefix="/content", tags=["content"])


def _to_blog_summary(post) -> PublicBlogPostSummary:
    return PublicBlogPostSummary(
        id=post.id,
        title=post.title,
        category=post.category,
        tag_key=post.tag_key,
        author_name=post.author_name,
        excerpt=post.excerpt,
        featured=post.featured,
        featured_image_url=media_url(post.featured_image) if post.featured_image else None,
        tags=post.tags,
        views=post.views,
        created_at=post.created_at,
    )


@router.get("/cms-blocks/{page}", response_model=list[PublicCmsBlock])
async def get_cms_blocks(page: str, db: AsyncSession = Depends(get_db)):
    """Public, unauthenticated read — powers Home/About/Contact/Footer/legal
    pages. Only is_active=True blocks are returned. Cache invalidation on
    every admin write (see cms_service.invalidate_page_cache) means this
    always reflects the latest edit, never the stale 5-minute TTL."""
    return await content_service.get_public_cms_blocks(db, page)


@router.get("/about-team", response_model=list[PublicTeamMember])
async def get_about_team(db: AsyncSession = Depends(get_db)):
    members = await content_service.list_team_members(db)
    return [
        PublicTeamMember(
            id=m.id,
            name=m.name,
            role=m.role,
            bio=m.bio,
            photo_url=media_url(m.photo) if m.photo else None,
            display_order=m.display_order,
        )
        for m in members
    ]


@router.get("/blog", response_model=list[PublicBlogPostSummary])
async def list_blog_posts(
    category: str | None = None,
    tag_key: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    posts = await content_service.list_published_posts(db, category=category, tag_key=tag_key)
    return [_to_blog_summary(p) for p in posts]


@router.get("/blog/{post_id}", response_model=PublicBlogPostDetail)
async def get_blog_post(post_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    post = await content_service.get_published_post(db, post_id)
    return PublicBlogPostDetail(
        id=post.id,
        title=post.title,
        category=post.category,
        tag_key=post.tag_key,
        author_name=post.author_name,
        excerpt=post.excerpt,
        featured=post.featured,
        featured_image_url=media_url(post.featured_image) if post.featured_image else None,
        tags=post.tags,
        views=post.views,
        created_at=post.created_at,
        content_html=post.content_html,
    )
