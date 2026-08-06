import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications import SupportTicket, TicketStatus
from app.models.user import User
from app.schemas.me import SupportTicketCreate
from app.worker.tasks.notifications import send_support_ticket_created


async def create_ticket(db: AsyncSession, user: User, data: SupportTicketCreate) -> SupportTicket:
    ticket = SupportTicket(
        user_id=user.id,
        subject=data.subject,
        category=data.category,
        priority=data.priority,
        status=TicketStatus.OPEN.value,
        message=data.message,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)

    send_support_ticket_created.delay(str(ticket.id))

    return ticket


async def list_tickets_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[SupportTicket]:
    result = await db.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id)
        .order_by(SupportTicket.created_at.desc())
    )
    return list(result.scalars().all())
