import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditActionType
from app.models.notifications import ContactSubmission, ContactSubmissionStatus
from app.models.user import User
from app.services.audit_service import write_audit_log
from app.services.email_service import email_service


async def list_submissions(
    db: AsyncSession, *, status_filter: str | None = None, search: str | None = None
) -> list[ContactSubmission]:
    query = select(ContactSubmission).order_by(ContactSubmission.created_at.desc())
    if status_filter:
        query = query.where(ContactSubmission.status == status_filter)
    if search:
        query = query.where(
            ContactSubmission.name.ilike(f"%{search}%")
            | ContactSubmission.email.ilike(f"%{search}%")
            | ContactSubmission.subject.ilike(f"%{search}%")
        )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_submission(db: AsyncSession, submission_id: uuid.UUID) -> ContactSubmission:
    result = await db.execute(
        select(ContactSubmission)
        .options(selectinload(ContactSubmission.replied_by))
        .where(ContactSubmission.id == submission_id)
    )
    submission = result.scalar_one_or_none()
    if submission is None:
        raise HTTPException(status_code=404, detail="Contact submission not found")
    return submission


async def reply_to_submission(
    db: AsyncSession,
    *,
    submission: ContactSubmission,
    reply_message: str,
    actor: User,
    ip_address: str | None,
) -> ContactSubmission:
    submission.reply_message = reply_message.strip()
    submission.replied_by_id = actor.id
    submission.replied_at = datetime.now(UTC)
    submission.status = ContactSubmissionStatus.REPLIED.value

    await write_audit_log(
        db,
        actor=actor,
        action="Replied to contact submission",
        action_type=AuditActionType.USER,
        target=f"{submission.name} ({submission.email})",
        ip_address=ip_address,
    )
    await db.commit()

    # expire_on_commit=False (see app.core.database) means `submission`
    # keeps whatever `replied_by` relationship state it had BEFORE this
    # reply — None, if this submission was loaded earlier in the same
    # request/session (which it always is: the router loads it via
    # get_submission before calling this function). Without expiring that
    # relationship explicitly, the get_submission() re-query below returns
    # the same identity-mapped object with a stale (unpopulated) `replied_by`
    # even though `replied_by_id` itself and every plain column are correct
    # — selectinload only repopulates relationships it considers unloaded,
    # and a previously-resolved-to-None relationship doesn't count as
    # unloaded. Bug found via test_admin_contact.py's
    # test_reply_sets_status_and_persists_fields, which failed with
    # replied_by_name=None on the reply response despite a correct DB row.
    db.expire(submission, ["replied_by"])

    # Sent synchronously (not via Celery) — matches how every other
    # already-real admin-triggered email in this codebase behaves
    # (email_service.send is already best-effort internally: a delivery
    # failure is logged, not raised, so this can't turn a successful reply
    # into a 500 for the admin — see email_service.send's docstring).
    email_service.send_contact_reply_email(
        submission.email,
        name=submission.name,
        original_subject=submission.subject,
        original_message=submission.message,
        reply_message=submission.reply_message,
    )

    return await get_submission(db, submission.id)
