from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditActionType, AuditLogEntry
from app.models.user import User


async def write_audit_log(
    db: AsyncSession,
    *,
    actor: User,
    action: str,
    action_type: AuditActionType,
    target: str,
    ip_address: str | None,
) -> None:
    db.add(
        AuditLogEntry(
            actor_id=actor.id,
            actor_name=actor.full_name,
            action=action,
            action_type=action_type.value,
            target=target,
            ip_address=ip_address,
        )
    )
