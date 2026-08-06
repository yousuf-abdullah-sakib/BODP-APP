import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditActionType
from app.models.catalog import Dataset, DatasetStatus
from app.models.requests import AccessGrant, GrantStatus
from app.models.user import User
from app.schemas.admin_datasets import DatasetCreate, DatasetUpdate
from app.services import requests_service
from app.services.audit_service import write_audit_log
from app.services.storage.registry import get_storage_backend


def _generate_code(sequence_hint: int) -> str:
    return f"BD-{sequence_hint:04d}"


async def list_datasets_for_admin(
    db: AsyncSession,
    *,
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    status_filter: str | None = None,
) -> list[Dataset]:
    # Admin view sees every status, unlike catalog_service's published-only
    # filter — draft/archived datasets must be manageable here too.
    query = select(Dataset).options(selectinload(Dataset.category)).order_by(Dataset.updated_at.desc())
    if search:
        query = query.where(Dataset.title.ilike(f"%{search}%"))
    if category_id:
        query = query.where(Dataset.category_id == category_id)
    if status_filter:
        query = query.where(Dataset.status == status_filter)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_dataset_for_admin(db: AsyncSession, dataset_id: uuid.UUID) -> Dataset:
    result = await db.execute(
        select(Dataset)
        .options(selectinload(Dataset.category), selectinload(Dataset.files))
        .where(Dataset.id == dataset_id)
    )
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


async def count_active_grants(db: AsyncSession, dataset_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(AccessGrant)
        .where(AccessGrant.dataset_id == dataset_id, AccessGrant.status == GrantStatus.ACTIVE.value)
    )
    return result.scalar_one()


async def create_dataset(
    db: AsyncSession, *, payload: DatasetCreate, actor: User, ip_address: str | None
) -> Dataset:
    code = payload.code
    if not code:
        count_result = await db.execute(select(Dataset))
        existing_count = len(count_result.scalars().all())
        code = _generate_code(existing_count + 1)

    dataset = Dataset(
        code=code,
        title=payload.title,
        description=payload.description,
        category_id=payload.category_id,
        location=payload.location,
        source=payload.source,
        platforms=payload.platforms,
        parameters=payload.parameters,
        resolution=payload.resolution,
        license=payload.license,
        processing_levels=payload.processing_levels,
        status=payload.status,
        created_by=actor.id,
    )
    db.add(dataset)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A dataset with this code already exists") from exc
    await db.refresh(dataset)

    await write_audit_log(
        db,
        actor=actor,
        action="Created dataset",
        action_type=AuditActionType.DATASET,
        target=dataset.title,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_dataset_for_admin(db, dataset.id)


async def update_dataset(
    db: AsyncSession, *, dataset: Dataset, payload: DatasetUpdate, actor: User, ip_address: str | None
) -> Dataset:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(dataset, field, value)

    await write_audit_log(
        db,
        actor=actor,
        action="Updated dataset",
        action_type=AuditActionType.DATASET,
        target=dataset.title,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_dataset_for_admin(db, dataset.id)


async def set_publish_status(
    db: AsyncSession, *, dataset: Dataset, published: bool, actor: User, ip_address: str | None
) -> Dataset:
    if dataset.status == DatasetStatus.ARCHIVED.value:
        raise HTTPException(
            status_code=409, detail="An archived dataset must be unarchived before it can be published"
        )
    dataset.status = DatasetStatus.PUBLISHED.value if published else DatasetStatus.DRAFT.value

    await write_audit_log(
        db,
        actor=actor,
        action=f"{'Published' if published else 'Unpublished'} dataset",
        action_type=AuditActionType.DATASET,
        target=dataset.title,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_dataset_for_admin(db, dataset.id)


async def archive_dataset(
    db: AsyncSession, *, dataset: Dataset, actor: User, ip_address: str | None
) -> Dataset:
    dataset.status = DatasetStatus.ARCHIVED.value

    active_grants = await requests_service.list_grants_for_dataset(
        db, dataset.id, status_filter=GrantStatus.ACTIVE.value
    )
    for grant in active_grants:
        await requests_service.revoke_grant(db, grant=grant, admin=actor, ip_address=ip_address)

    await write_audit_log(
        db,
        actor=actor,
        action=f"Archived dataset — revoked {len(active_grants)} active grant(s)",
        action_type=AuditActionType.DATASET,
        target=dataset.title,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_dataset_for_admin(db, dataset.id)


async def unarchive_dataset(
    db: AsyncSession, *, dataset: Dataset, actor: User, ip_address: str | None
) -> Dataset:
    if dataset.status != DatasetStatus.ARCHIVED.value:
        raise HTTPException(status_code=409, detail="Dataset is not archived")
    # Back to draft, never straight to published — forces an explicit
    # re-publish step rather than silently re-exposing the dataset.
    dataset.status = DatasetStatus.DRAFT.value

    await write_audit_log(
        db,
        actor=actor,
        action="Unarchived dataset",
        action_type=AuditActionType.DATASET,
        target=dataset.title,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_dataset_for_admin(db, dataset.id)


async def permanently_delete_dataset(
    db: AsyncSession, *, dataset: Dataset, actor: User, ip_address: str | None
) -> None:
    active_grants = await requests_service.list_grants_for_dataset(
        db, dataset.id, status_filter=GrantStatus.ACTIVE.value
    )
    for grant in active_grants:
        await requests_service.revoke_grant(db, grant=grant, admin=actor, ip_address=ip_address)

    for file in dataset.files:
        storage = get_storage_backend(file.storage_backend)
        storage.delete(file.storage_bucket, file.storage_key)
        meta = file.file_metadata or {}
        processed_key = meta.get("processed_key")
        processed_bucket = meta.get("processed_bucket")
        if processed_key and processed_bucket:
            storage.delete(processed_bucket, processed_key)

    await write_audit_log(
        db,
        actor=actor,
        action=f"Permanently deleted dataset — revoked {len(active_grants)} active grant(s)",
        action_type=AuditActionType.DATASET,
        target=f"{dataset.title} ({dataset.code})",
        ip_address=ip_address,
    )
    # Cascades DatasetFile/DatasetRecord at both ORM (cascade="all,
    # delete-orphan") and DB (ondelete="CASCADE") level.
    await db.delete(dataset)
    await db.commit()
