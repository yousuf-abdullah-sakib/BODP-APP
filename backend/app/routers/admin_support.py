import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_support import (
    SupportTicketAdminDetail,
    SupportTicketAdminSummary,
    SupportTicketReplyCreate,
)
from app.services import admin_support_service

router = APIRouter(prefix="/admin/support-tickets", tags=["admin-support"])


def _to_summary(ticket) -> SupportTicketAdminSummary:
    return SupportTicketAdminSummary(
        id=ticket.id,
        subject=ticket.subject,
        category=ticket.category,
        priority=ticket.priority,
        status=ticket.status,
        requester_name=ticket.user.full_name,
        requester_email=ticket.user.email,
        created_at=ticket.created_at,
    )


def _to_detail(ticket) -> SupportTicketAdminDetail:
    return SupportTicketAdminDetail(
        id=ticket.id,
        subject=ticket.subject,
        category=ticket.category,
        priority=ticket.priority,
        status=ticket.status,
        message=ticket.message,
        requester_name=ticket.user.full_name,
        requester_email=ticket.user.email,
        reply_message=ticket.reply_message,
        replied_by_name=ticket.replied_by.full_name if ticket.replied_by else None,
        replied_at=ticket.replied_at,
        created_at=ticket.created_at,
    )


@router.get("", response_model=list[SupportTicketAdminSummary])
async def list_tickets(
    status_filter: str | None = None,
    search: str | None = None,
    current_user: User = Depends(require_permission("Manage Support")),
    db: AsyncSession = Depends(get_db),
):
    tickets = await admin_support_service.list_tickets(db, status_filter=status_filter, search=search)
    return [_to_summary(t) for t in tickets]


@router.get("/{ticket_id}", response_model=SupportTicketAdminDetail)
async def get_ticket(
    ticket_id: uuid.UUID,
    current_user: User = Depends(require_permission("Manage Support")),
    db: AsyncSession = Depends(get_db),
):
    ticket = await admin_support_service.get_ticket(db, ticket_id)
    return _to_detail(ticket)


@router.post("/{ticket_id}/reply", response_model=SupportTicketAdminDetail)
async def reply_to_ticket(
    ticket_id: uuid.UUID,
    payload: SupportTicketReplyCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Support")),
    db: AsyncSession = Depends(get_db),
):
    ticket = await admin_support_service.get_ticket(db, ticket_id)
    ticket = await admin_support_service.reply_to_ticket(
        db,
        ticket=ticket,
        reply_message=payload.reply_message,
        actor=current_user,
        ip_address=get_client_ip(request),
    )
    return _to_detail(ticket)
