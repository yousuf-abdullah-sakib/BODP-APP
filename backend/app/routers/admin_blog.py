import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_blog import (
    BlogPostAdminDetail,
    BlogPostAdminSummary,
    BlogPostCreate,
    BlogPostUpdate,
)
from app.services import admin_blog_service
from app.services.admin_media_service import media_url

router = APIRouter(prefix="/admin/blog", tags=["admin-blog"])


def _to_summary(post) -> BlogPostAdminSummary:
    return BlogPostAdminSummary(
        id=post.id,
        title=post.title,
        category=post.category,
        author_name=post.author_name,
        status=post.status,
        featured=post.featured,
        views=post.views,
        featured_image_url=media_url(post.featured_image) if post.featured_image else None,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


def _to_detail(post) -> BlogPostAdminDetail:
    return BlogPostAdminDetail(
        id=post.id,
        title=post.title,
        category=post.category,
        tag_key=post.tag_key,
        author_name=post.author_name,
        excerpt=post.excerpt,
        content_html=post.content_html,
        status=post.status,
        featured=post.featured,
        featured_image_id=post.featured_image_id,
        featured_image_url=media_url(post.featured_image) if post.featured_image else None,
        tags=post.tags,
        views=post.views,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


@router.get("", response_model=list[BlogPostAdminSummary])
async def list_posts(
    search: str | None = None,
    status_filter: str | None = None,
    current_user: User = Depends(require_permission("Manage Blog")),
    db: AsyncSession = Depends(get_db),
):
    posts = await admin_blog_service.list_posts(db, search=search, status_filter=status_filter)
    return [_to_summary(p) for p in posts]


@router.get("/{post_id}", response_model=BlogPostAdminDetail)
async def get_post(
    post_id: uuid.UUID,
    current_user: User = Depends(require_permission("Manage Blog")),
    db: AsyncSession = Depends(get_db),
):
    post = await admin_blog_service.get_post(db, post_id)
    return _to_detail(post)


@router.post("", response_model=BlogPostAdminDetail, status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: BlogPostCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Blog")),
    db: AsyncSession = Depends(get_db),
):
    post = await admin_blog_service.create_post(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_detail(post)


@router.patch("/{post_id}", response_model=BlogPostAdminDetail)
async def update_post(
    post_id: uuid.UUID,
    payload: BlogPostUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Blog")),
    db: AsyncSession = Depends(get_db),
):
    post = await admin_blog_service.get_post(db, post_id)
    post = await admin_blog_service.update_post(
        db, post=post, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_detail(post)


@router.post("/{post_id}/publish", response_model=BlogPostAdminDetail)
async def publish_post(
    post_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Blog")),
    db: AsyncSession = Depends(get_db),
):
    post = await admin_blog_service.get_post(db, post_id)
    post = await admin_blog_service.set_status(
        db, post=post, published=True, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_detail(post)


@router.post("/{post_id}/unpublish", response_model=BlogPostAdminDetail)
async def unpublish_post(
    post_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Blog")),
    db: AsyncSession = Depends(get_db),
):
    post = await admin_blog_service.get_post(db, post_id)
    post = await admin_blog_service.set_status(
        db, post=post, published=False, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_detail(post)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(
    post_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Blog")),
    db: AsyncSession = Depends(get_db),
):
    post = await admin_blog_service.get_post(db, post_id)
    await admin_blog_service.delete_post(
        db, post=post, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
