import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_roles import RoleCreate, RolePublic, RoleUpdate
from app.services import admin_roles_service

router = APIRouter(prefix="/admin/roles", tags=["admin-roles"])


def _to_public(role, user_count: int) -> RolePublic:
    return RolePublic(
        id=role.id,
        name=role.name,
        description=role.description,
        permissions=role.permissions,
        user_count=user_count,
    )


@router.get("", response_model=list[RolePublic])
async def list_roles(
    current_user: User = Depends(require_permission("Manage Roles")),
    db: AsyncSession = Depends(get_db),
):
    rows = await admin_roles_service.list_roles(db)
    return [_to_public(role, count) for role, count in rows]


@router.post("", response_model=RolePublic, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Roles")),
    db: AsyncSession = Depends(get_db),
):
    role = await admin_roles_service.create_role(
        db, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    return _to_public(role, 0)


@router.patch("/{role_id}", response_model=RolePublic)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Roles")),
    db: AsyncSession = Depends(get_db),
):
    role = await admin_roles_service.get_role(db, role_id)
    role = await admin_roles_service.update_role(
        db, role=role, payload=payload, actor=current_user, ip_address=get_client_ip(request)
    )
    rows = await admin_roles_service.list_roles(db)
    count = next((c for r, c in rows if r.id == role.id), 0)
    return _to_public(role, count)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_permission("Manage Roles")),
    db: AsyncSession = Depends(get_db),
):
    role = await admin_roles_service.get_role(db, role_id)
    await admin_roles_service.delete_role(
        db, role=role, actor=current_user, ip_address=get_client_ip(request)
    )
    return None
