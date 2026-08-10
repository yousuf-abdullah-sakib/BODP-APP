import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditActionType
from app.models.notifications import Notification, NotificationType, SupportTicket, TicketStatus
from app.models.user import User
from app.services.audit_service import write_audit_log


async def list_tickets(
    db: AsyncSession, *, status_filter: str | None = None, search: str | None = None
) -> list[SupportTicket]:
    query = (
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .order_by(SupportTicket.created_at.desc())
    )
    if status_filter:
        query = query.where(SupportTicket.status == status_filter)
    if search:
        query = query.where(SupportTicket.subject.ilike(f"%{search}%"))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_ticket(db: AsyncSession, ticket_id: uuid.UUID) -> SupportTicket:
    result = await db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user), selectinload(SupportTicket.replied_by))
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    return ticket


async def reply_to_ticket(
    db: AsyncSession,
    *,
    ticket: SupportTicket,
    reply_message: str,
    actor: User,
    ip_address: str | None,
) -> SupportTicket:
    ticket.reply_message = reply_message.strip()
    ticket.replied_by_id = actor.id
    ticket.replied_at = datetime.now(UTC)
    ticket.status = TicketStatus.ANSWERED.value

    # In-app notification for the submitter — this system deliberately
    # does not use email (see Master Plan scope for this feature): the
    # reply shows up in the user's own Contact Support dashboard section,
    # surfaced the same way every other user-facing event already is.
    db.add(
        Notification(
            user_id=ticket.user_id,
            type=NotificationType.INFO.value,
            title="Your support ticket was answered",
            description=f"An admin replied to your ticket: \"{ticket.subject}\".",
        )
    )

    await write_audit_log(
        db,
        actor=actor,
        action="Replied to support ticket",
        action_type=AuditActionType.USER,
        target=ticket.subject,
        ip_address=ip_address,
    )
    await db.commit()

    # expire_on_commit=False (see app.core.database) means `ticket` keeps
    # whatever `replied_by` relationship state it had BEFORE this reply —
    # None, since the router always loads it via get_ticket earlier in the
    # same request/session. Without expiring that relationship explicitly,
    # the get_ticket() re-query below returns the same identity-mapped
    # object with a stale (unpopulated) `replied_by` even though
    # `replied_by_id` and every plain column are correct — selectinload only
    # repopulates relationships it considers unloaded, and a
    # previously-resolved-to-None relationship doesn't count as unloaded.
    # Same bug and fix as admin_contact_service.reply_to_submission.
    db.expire(ticket, ["replied_by"])

    return await get_ticket(db, ticket.id)
