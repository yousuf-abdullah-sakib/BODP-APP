import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import AboutTeamMember
from app.models.audit import AuditActionType
from app.models.user import User
from app.schemas.admin_about_team import AboutTeamMemberCreate, AboutTeamMemberUpdate
from app.services.audit_service import write_audit_log


async def list_members(db: AsyncSession) -> list[AboutTeamMember]:
    result = await db.execute(
        select(AboutTeamMember)
        .options(selectinload(AboutTeamMember.photo))
        .order_by(AboutTeamMember.display_order, AboutTeamMember.name)
    )
    return list(result.scalars().all())


async def get_member(db: AsyncSession, member_id: uuid.UUID) -> AboutTeamMember:
    result = await db.execute(
        select(AboutTeamMember)
        .options(selectinload(AboutTeamMember.photo))
        .where(AboutTeamMember.id == member_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Team member not found")
    return member


async def create_member(
    db: AsyncSession, *, payload: AboutTeamMemberCreate, actor: User, ip_address: str | None
) -> AboutTeamMember:
    member = AboutTeamMember(
        name=payload.name,
        role=payload.role,
        bio=payload.bio,
        photo_id=payload.photo_id,
        display_order=payload.display_order,
    )
    db.add(member)
    await write_audit_log(
        db,
        actor=actor,
        action="Created team member",
        action_type=AuditActionType.CONTENT,
        target=member.name,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_member(db, member.id)


async def update_member(
    db: AsyncSession,
    *,
    member: AboutTeamMember,
    payload: AboutTeamMemberUpdate,
    actor: User,
    ip_address: str | None,
) -> AboutTeamMember:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(member, field, value)

    await write_audit_log(
        db,
        actor=actor,
        action="Updated team member",
        action_type=AuditActionType.CONTENT,
        target=member.name,
        ip_address=ip_address,
    )
    await db.commit()
    return await get_member(db, member.id)


async def delete_member(
    db: AsyncSession, *, member: AboutTeamMember, actor: User, ip_address: str | None
) -> None:
    await write_audit_log(
        db,
        actor=actor,
        action="Deleted team member",
        action_type=AuditActionType.CONTENT,
        target=member.name,
        ip_address=ip_address,
    )
    await db.delete(member)
    await db.commit()
