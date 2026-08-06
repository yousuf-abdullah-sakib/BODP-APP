import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_cms import CmsBlockCreate, CmsBlockPublic, CmsBlockUpdate
from app.services import admin_cms_service

router = APIRouter(prefix="/admin/cms", tags=["admin-cms"])


@router.get("/blocks", response_model=list[CmsBlockPublic])
async def list_blocks(
    page: str | None = None,
    current_user: User = Depends(require_permission("Manage CMS")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_cms_service.list_blocks(db, page=page)


@router.post("/blocks", response_model=CmsBlockPublic, status_code=status.HTTP_201_CREATED)
async def create_block(
    payload: CmsBlockCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage CMS")),
    db: AsyncSession = Depends(get_db),
):
    return await admin_cms_service.create_block(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )


@router.patch("/blocks/{block_id}", response_model=CmsBlockPublic)
async def update_block(
    block_id: uuid.UUID,
    payload: CmsBlockUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Manage CMS")),
    db: AsyncSession = Depends(get_db),
):
    block = await admin_cms_service.get_block(db, block_id)
    return await admin_cms_service.update_block(
        db, block=block, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )


@router.delete("/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_block(
    block_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage CMS")),
    db: AsyncSession = Depends(get_db),
):
    block = await admin_cms_service.get_block(db, block_id)
    await admin_cms_service.delete_block(
        db, block=block, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
