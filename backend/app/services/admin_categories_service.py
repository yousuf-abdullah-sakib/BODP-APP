import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditActionType
from app.models.catalog import Dataset, DatasetCategory
from app.models.user import User
from app.schemas.admin_categories import CategoryCreate, CategoryUpdate
from app.services.audit_service import write_audit_log


async def list_categories(db: AsyncSession) -> list[tuple[DatasetCategory, int]]:
    result = await db.execute(
        select(DatasetCategory, func.count(Dataset.id))
        .outerjoin(Dataset, Dataset.category_id == DatasetCategory.id)
        .group_by(DatasetCategory.id)
        .order_by(DatasetCategory.name)
    )
    return [(row[0], row[1]) for row in result.all()]


async def get_category(db: AsyncSession, category_id: uuid.UUID) -> DatasetCategory:
    category = await db.get(DatasetCategory, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


async def create_category(
    db: AsyncSession, *, payload: CategoryCreate, actor: User, ip_address: str | None
) -> DatasetCategory:
    category = DatasetCategory(
        name=payload.name, description=payload.description, color_tag=payload.color_tag
    )
    db.add(category)
    try:
        await db.flush()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A category with this name already exists") from exc

    await write_audit_log(
        db,
        actor=actor,
        action="Created dataset category",
        action_type=AuditActionType.CONTENT,
        target=category.name,
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(category)
    return category


async def update_category(
    db: AsyncSession,
    *,
    category: DatasetCategory,
    payload: CategoryUpdate,
    actor: User,
    ip_address: str | None,
) -> DatasetCategory:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(category, field, value)

    await write_audit_log(
        db,
        actor=actor,
        action="Updated dataset category",
        action_type=AuditActionType.CONTENT,
        target=category.name,
        ip_address=ip_address,
    )
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A category with this name already exists") from exc
    await db.refresh(category)
    return category


async def delete_category(
    db: AsyncSession, *, category: DatasetCategory, actor: User, ip_address: str | None
) -> None:
    dataset_count = await db.scalar(
        select(func.count()).select_from(Dataset).where(Dataset.category_id == category.id)
    )

    await write_audit_log(
        db,
        actor=actor,
        action=f"Deleted dataset category — {dataset_count or 0} dataset(s) uncategorized",
        action_type=AuditActionType.CONTENT,
        target=category.name,
        ip_address=ip_address,
    )
    # Dataset.category_id's ondelete="SET NULL" nullifies attached datasets
    # automatically at the DB level — no manual UPDATE needed here.
    await db.delete(category)
    await db.commit()
