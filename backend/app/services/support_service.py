import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications import SupportTicket, TicketStatus
from app.models.user import User
from app.schemas.me import SupportTicketCreate
from app.services.admin_notify_service import notify_admins_with_permission
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
    await db.flush()

    # In-app notification for "Manage Support" admins — this used to be the
    # ONLY dispatch (send_support_ticket_created below is a best-effort
    # single email to whatever SiteSettings.contact_email happens to be
    # configured, which silently skips entirely if it's unset), so a
    # submitted ticket had no guaranteed human-visible surface at all.
    await notify_admins_with_permission(
        db,
        permission="Manage Support",
        type="info",
        title="New support ticket",
        description=f"{user.full_name} submitted a ticket: \"{ticket.subject}\".",
    )

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
