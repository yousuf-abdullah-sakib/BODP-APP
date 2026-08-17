import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_dataset_schema import (
    DatasetFileStorageSummary,
    DatasetSchemaDetail,
    DatasetSchemaReviewResult,
    DatasetSchemaReviewSummary,
    DatasetVariablePublic,
    VariableRoleUpdate,
)
from app.services import admin_dataset_schema_service

router = APIRouter(prefix="/admin/dataset-schema", tags=["admin-dataset-schema"])

_PERMISSION = "Review Datasets"


@router.get("", response_model=list[DatasetSchemaReviewSummary])
async def list_datasets_for_review(
    unreviewed_only: bool = False,
    current_user: User = Depends(require_permission(_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    rows = await admin_dataset_schema_service.list_datasets_for_review(
        db, unreviewed_only=unreviewed_only
    )
    return [DatasetSchemaReviewSummary(**row) for row in rows]


@router.get("/{dataset_id}", response_model=DatasetSchemaDetail)
async def get_dataset_schema(
    dataset_id: uuid.UUID,
    current_user: User = Depends(require_permission(_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    data = await admin_dataset_schema_service.get_dataset_schema(db, dataset_id)
    return DatasetSchemaDetail(
        dataset_id=data["dataset_id"],
        dataset_code=data["dataset_code"],
        dataset_title=data["dataset_title"],
        schema_reviewed_at=data["schema_reviewed_at"],
        schema_reviewed_by_name=data["schema_reviewed_by_name"],
        variables=[DatasetVariablePublic.model_validate(v) for v in data["variables"]],
        files=[DatasetFileStorageSummary.model_validate(f) for f in data["files"]],
    )


@router.patch("/{dataset_id}/variables/{variable_id}", response_model=DatasetVariablePublic)
async def update_variable_roles(
    dataset_id: uuid.UUID,
    variable_id: uuid.UUID,
    payload: VariableRoleUpdate,
    request: Request,
    current_user: User = Depends(require_permission(_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    variable = await admin_dataset_schema_service.update_variable_roles(
        db,
        dataset_id=dataset_id,
        variable_id=variable_id,
        roles=payload.roles,
        actor=current_user,
        ip_address=get_client_ip(request),
    )
    return variable


@router.post("/{dataset_id}/mark-reviewed", response_model=DatasetSchemaReviewResult)
async def mark_dataset_reviewed(
    dataset_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission(_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    dataset = await admin_dataset_schema_service.mark_dataset_reviewed(
        db, dataset_id=dataset_id, actor=current_user, ip_address=get_client_ip(request)
    )
    return DatasetSchemaReviewResult(
        dataset_id=dataset.id,
        schema_reviewed_at=dataset.schema_reviewed_at,
        schema_reviewed_by_name=current_user.full_name,
    )
