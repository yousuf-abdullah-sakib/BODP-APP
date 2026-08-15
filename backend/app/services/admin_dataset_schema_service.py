import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditActionType
from app.models.catalog import Dataset, DatasetVariable
from app.models.user import User
from app.services.audit_service import write_audit_log


async def list_datasets_for_review(db: AsyncSession, *, unreviewed_only: bool) -> list[dict]:
    """One row per dataset that has at least one detected variable (Phase
    2's ingestion is what creates DatasetVariable rows — a dataset with no
    files yet has nothing to review). `unreviewed_only` mirrors the
    Requests queue's pending/all tab split."""
    query = (
        select(
            Dataset,
            func.count(DatasetVariable.id).label("variable_count"),
            func.count(DatasetVariable.id)
            .filter(func.cardinality(DatasetVariable.roles) == 0)
            .label("unassigned_count"),
        )
        .join(DatasetVariable, DatasetVariable.dataset_id == Dataset.id)
        .group_by(Dataset.id)
        .order_by(Dataset.updated_at.desc())
    )
    if unreviewed_only:
        query = query.where(Dataset.schema_reviewed_at.is_(None))

    rows = (await db.execute(query)).all()

    reviewer_ids = {d.schema_reviewed_by for d, _, _ in rows if d.schema_reviewed_by is not None}
    reviewers: dict[uuid.UUID, User] = {}
    if reviewer_ids:
        result = await db.execute(select(User).where(User.id.in_(reviewer_ids)))
        reviewers = {u.id: u for u in result.scalars().all()}

    return [
        {
            "dataset_id": dataset.id,
            "dataset_code": dataset.code,
            "dataset_title": dataset.title,
            "variable_count": variable_count,
            "unassigned_count": unassigned_count,
            "schema_reviewed_at": dataset.schema_reviewed_at,
            "schema_reviewed_by_name": (
                reviewers[dataset.schema_reviewed_by].full_name
                if dataset.schema_reviewed_by in reviewers
                else None
            ),
        }
        for dataset, variable_count, unassigned_count in rows
    ]


async def get_dataset_schema(db: AsyncSession, dataset_id: uuid.UUID) -> dict:
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    result = await db.execute(
        select(DatasetVariable)
        .where(DatasetVariable.dataset_id == dataset_id)
        .order_by(DatasetVariable.name)
    )
    variables = list(result.scalars().all())

    reviewer_name = None
    if dataset.schema_reviewed_by is not None:
        reviewer = await db.get(User, dataset.schema_reviewed_by)
        reviewer_name = reviewer.full_name if reviewer else None

    return {
        "dataset_id": dataset.id,
        "dataset_code": dataset.code,
        "dataset_title": dataset.title,
        "schema_reviewed_at": dataset.schema_reviewed_at,
        "schema_reviewed_by_name": reviewer_name,
        "variables": variables,
    }


async def _get_variable_for_dataset(
    db: AsyncSession, dataset_id: uuid.UUID, variable_id: uuid.UUID
) -> DatasetVariable:
    variable = await db.get(DatasetVariable, variable_id)
    if variable is None or variable.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="Variable not found")
    return variable


async def update_variable_roles(
    db: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    variable_id: uuid.UUID,
    roles: list[str],
    actor: User,
    ip_address: str | None,
) -> DatasetVariable:
    """Setting a variable's roles un-does any prior full-dataset approval —
    the dataset must be explicitly re-approved (mark_reviewed) after its
    schema changes, rather than silently staying "reviewed" against a
    schema an admin no longer agrees with."""
    variable = await _get_variable_for_dataset(db, dataset_id, variable_id)
    variable.roles = roles

    dataset = await db.get(Dataset, dataset_id)
    dataset.schema_reviewed_at = None
    dataset.schema_reviewed_by = None

    await write_audit_log(
        db,
        actor=actor,
        action=f"Set roles for variable '{variable.name}' on dataset '{dataset.title}'",
        action_type=AuditActionType.DATASET,
        target=f"dataset:{dataset.id}",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(variable)
    return variable


async def mark_dataset_reviewed(
    db: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    actor: User,
    ip_address: str | None,
) -> Dataset:
    dataset = await db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    result = await db.execute(
        select(func.count()).select_from(DatasetVariable).where(
            DatasetVariable.dataset_id == dataset_id,
            func.cardinality(DatasetVariable.roles) == 0,
        )
    )
    unassigned_count = result.scalar_one()
    if unassigned_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"{unassigned_count} variable(s) still have no assigned role",
        )

    dataset.schema_reviewed_at = datetime.now(UTC)
    dataset.schema_reviewed_by = actor.id

    await write_audit_log(
        db,
        actor=actor,
        action=f"Approved schema review for dataset '{dataset.title}'",
        action_type=AuditActionType.DATASET,
        target=f"dataset:{dataset.id}",
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(dataset)
    return dataset
