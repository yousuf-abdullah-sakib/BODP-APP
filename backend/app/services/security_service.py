import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import ActiveSession


async def list_sessions(
    db: AsyncSession, user_id: uuid.UUID, *, current_session_id: uuid.UUID | None
) -> list[tuple[ActiveSession, bool]]:
    result = await db.execute(
        select(ActiveSession)
        .where(ActiveSession.user_id == user_id, ActiveSession.revoked_at.is_(None))
        .order_by(ActiveSession.last_active_at.desc())
    )
    sessions = result.scalars().all()
    return [(s, s.id == current_session_id) for s in sessions]


async def revoke_session(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
    result = await db.execute(
        select(ActiveSession).where(ActiveSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.is_revoked:
        session.revoked_at = datetime.now(UTC)
        await db.commit()
