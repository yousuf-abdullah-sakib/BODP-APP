import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_client_ip
from app.core.permissions import require_permission
from app.models.user import User
from app.schemas.admin_contact import (
    ContactReplyCreate,
    ContactSubmissionAdminDetail,
    ContactSubmissionAdminSummary,
)
from app.services import admin_contact_service

router = APIRouter(prefix="/admin/contact", tags=["admin-contact"])


def _to_summary(submission) -> ContactSubmissionAdminSummary:
    return ContactSubmissionAdminSummary(
        id=submission.id,
        name=submission.name,
        email=submission.email,
        organization=submission.organization,
        subject=submission.subject,
        status=submission.status,
        created_at=submission.created_at,
    )


def _to_detail(submission) -> ContactSubmissionAdminDetail:
    return ContactSubmissionAdminDetail(
        id=submission.id,
        name=submission.name,
        email=submission.email,
        organization=submission.organization,
        subject=submission.subject,
        message=submission.message,
        status=submission.status,
        reply_message=submission.reply_message,
        replied_by_name=submission.replied_by.full_name if submission.replied_by else None,
        replied_at=submission.replied_at,
        created_at=submission.created_at,
    )


@router.get("", response_model=list[ContactSubmissionAdminSummary])
async def list_submissions(
    status_filter: str | None = None,
    search: str | None = None,
    current_user: User = Depends(require_permission("Manage Support")),
    db: AsyncSession = Depends(get_db),
):
    submissions = await admin_contact_service.list_submissions(
        db, status_filter=status_filter, search=search
    )
    return [_to_summary(s) for s in submissions]


@router.get("/{submission_id}", response_model=ContactSubmissionAdminDetail)
async def get_submission(
    submission_id: uuid.UUID,
    current_user: User = Depends(require_permission("Manage Support")),
    db: AsyncSession = Depends(get_db),
):
    submission = await admin_contact_service.get_submission(db, submission_id)
    return _to_detail(submission)


@router.post("/{submission_id}/reply", response_model=ContactSubmissionAdminDetail)
async def reply_to_submission(
    submission_id: uuid.UUID,
    payload: ContactReplyCreate,
    request: Request,
    current_user: User = Depends(require_permission("Manage Support")),
    db: AsyncSession = Depends(get_db),
):
    submission = await admin_contact_service.get_submission(db, submission_id)
    submission = await admin_contact_service.reply_to_submission(
        db,
        submission=submission,
        reply_message=payload.reply_message,
        actor=current_user,
        ip_address=get_client_ip(request),
    )
    return _to_detail(submission)
