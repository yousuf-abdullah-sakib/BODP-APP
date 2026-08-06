import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_categories import CategoryCreate, CategoryPublic, CategoryUpdate
from app.services import admin_categories_service

router = APIRouter(prefix="/admin/categories", tags=["admin-categories"])


def _to_public(category, dataset_count: int) -> CategoryPublic:
    return CategoryPublic(
        id=category.id,
        name=category.name,
        description=category.description,
        color_tag=category.color_tag,
        dataset_count=dataset_count,
    )


@router.get("", response_model=list[CategoryPublic])
async def list_categories(
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    rows = await admin_categories_service.list_categories(db)
    return [_to_public(category, count) for category, count in rows]


@router.post("", response_model=CategoryPublic, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate,
    request: Request,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    category = await admin_categories_service.create_category(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_public(category, 0)


@router.patch("/{category_id}", response_model=CategoryPublic)
async def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    category = await admin_categories_service.get_category(db, category_id)
    category = await admin_categories_service.update_category(
        db, category=category, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    rows = await admin_categories_service.list_categories(db)
    count = next((c for cat, c in rows if cat.id == category.id), 0)
    return _to_public(category, count)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Edit Datasets")),
    db: AsyncSession = Depends(get_db),
):
    category = await admin_categories_service.get_category(db, category_id)
    await admin_categories_service.delete_category(
        db, category=category, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
