import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.models.admin import SiteSettings
from app.models.catalog import Dataset
from app.models.notifications import Notification, NotificationType, SupportTicket
from app.models.requests import AccessGrant, DatasetRequest, ExtractionStatus, GrantStatus, SubsetExtraction
from app.models.user import Role, User, UserRole
from app.services.email_service import email_service
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)

_EXPIRING_SOON_DAYS = 7
_DELETION_GRACE_PERIOD_DAYS = 30


def _admins_with_permission(db, permission: str) -> list[User]:
    result = db.execute(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.permissions.any(permission))
        .distinct()
    )
    return list(result.scalars().all())


@celery_app.task(name="notifications.send_request_submitted", bind=True, max_retries=2)
def send_request_submitted(self, request_id: str) -> dict:
    """Notifies every admin holding the "Approve Requests" permission that a
    new dataset request needs review (Master Plan §3 Phase 4 task 2)."""
    with get_sync_db() as db:
        request = db.get(DatasetRequest, uuid.UUID(request_id))
        if request is None:
            logger.error("notifications.request_not_found", request_id=request_id)
            return {"status": "failed", "reason": "request not found"}

        requester = db.get(User, request.user_id)
        dataset = db.get(Dataset, request.dataset_id)
        admins = _admins_with_permission(db, "Approve Requests")

        for admin in admins:
            db.add(
                Notification(
                    user_id=admin.id,
                    type=NotificationType.INFO.value,
                    title="New dataset access request",
                    description=f"{requester.full_name if requester else 'A researcher'} "
                    f"requested access to \"{dataset.title if dataset else 'a dataset'}\".",
                )
            )
        db.commit()

        for admin in admins:
            try:
                email_service.send_request_submitted_email(
                    admin.email,
                    admin_name=admin.full_name,
                    requester_name=requester.full_name if requester else "A researcher",
                    dataset_title=dataset.title if dataset else "a dataset",
                )
            except Exception:
                logger.exception("notifications.email_send_failed", to=admin.email)

        return {"status": "complete", "notified_admins": len(admins)}


@celery_app.task(name="notifications.send_request_approved", bind=True, max_retries=2)
def send_request_approved(self, request_id: str) -> dict:
    with get_sync_db() as db:
        request = db.get(DatasetRequest, uuid.UUID(request_id))
        if request is None:
            logger.error("notifications.request_not_found", request_id=request_id)
            return {"status": "failed", "reason": "request not found"}

        requester = db.get(User, request.user_id)
        dataset = db.get(Dataset, request.dataset_id)
        if requester is None:
            return {"status": "failed", "reason": "requester not found"}

        db.add(
            Notification(
                user_id=requester.id,
                type=NotificationType.SUCCESS.value,
                title="Dataset access request approved",
                description=f"Your request for \"{dataset.title if dataset else 'a dataset'}\" was approved.",
            )
        )
        db.commit()

        try:
            email_service.send_request_approved_email(
                requester.email,
                full_name=requester.full_name,
                dataset_title=dataset.title if dataset else "a dataset",
            )
        except Exception:
            logger.exception("notifications.email_send_failed", to=requester.email)

        return {"status": "complete"}


@celery_app.task(name="notifications.send_request_rejected", bind=True, max_retries=2)
def send_request_rejected(self, request_id: str) -> dict:
    with get_sync_db() as db:
        request = db.get(DatasetRequest, uuid.UUID(request_id))
        if request is None:
            logger.error("notifications.request_not_found", request_id=request_id)
            return {"status": "failed", "reason": "request not found"}

        requester = db.get(User, request.user_id)
        dataset = db.get(Dataset, request.dataset_id)
        if requester is None:
            return {"status": "failed", "reason": "requester not found"}

        db.add(
            Notification(
                user_id=requester.id,
                type=NotificationType.DANGER.value,
                title="Dataset access request rejected",
                description=f"Your request for \"{dataset.title if dataset else 'a dataset'}\" "
                f"was rejected: {request.admin_note or 'No reason provided.'}",
            )
        )
        db.commit()

        try:
            email_service.send_request_rejected_email(
                requester.email,
                full_name=requester.full_name,
                dataset_title=dataset.title if dataset else "a dataset",
                reason=request.admin_note or "No reason provided.",
            )
        except Exception:
            logger.exception("notifications.email_send_failed", to=requester.email)

        return {"status": "complete"}


@celery_app.task(name="notifications.check_expiring_grants", bind=True, max_retries=2)
def check_expiring_grants(self) -> dict:
    """Daily scan (Celery beat, Master Plan §3 Phase 4 task 9) — notifies
    grantees whose access expires within _EXPIRING_SOON_DAYS. Re-notifies
    daily for the remainder of the window rather than deduplicating, matching
    typical "expiring soon" reminder UX; a one-time-only reminder would need
    a dedup table this phase doesn't otherwise need."""
    now = datetime.now(UTC)
    threshold = now + timedelta(days=_EXPIRING_SOON_DAYS)

    with get_sync_db() as db:
        result = db.execute(
            select(AccessGrant).where(
                AccessGrant.status == GrantStatus.ACTIVE.value,
                AccessGrant.expires_at <= threshold,
                AccessGrant.expires_at > now,
            )
        )
        grants = list(result.scalars().all())

        notified = 0
        for grant in grants:
            grantee = db.get(User, grant.user_id)
            dataset = db.get(Dataset, grant.dataset_id)
            if grantee is None:
                continue

            db.add(
                Notification(
                    user_id=grantee.id,
                    type=NotificationType.WARNING.value,
                    title="Dataset access expiring soon",
                    description=f"Your access to \"{dataset.title if dataset else 'a dataset'}\" "
                    f"expires on {grant.expires_at.date().isoformat()}.",
                )
            )
            try:
                email_service.send_grant_expiring_email(
                    grantee.email,
                    full_name=grantee.full_name,
                    dataset_title=dataset.title if dataset else "a dataset",
                    expires_at=grant.expires_at.date().isoformat(),
                )
            except Exception:
                logger.exception("notifications.email_send_failed", to=grantee.email)
            notified += 1

        db.commit()
        return {"status": "complete", "notified": notified}


@celery_app.task(name="notifications.send_support_ticket_created", bind=True, max_retries=2)
def send_support_ticket_created(self, ticket_id: str) -> dict:
    """Emails the configured support contact when a user submits a ticket
    (Master Plan §3 Phase 6 task 6). Skips silently (logged) if no contact
    email is configured — this is not a hard dependency for ticket creation
    to succeed."""
    with get_sync_db() as db:
        ticket = db.get(SupportTicket, uuid.UUID(ticket_id))
        if ticket is None:
            logger.error("notifications.ticket_not_found", ticket_id=ticket_id)
            return {"status": "failed", "reason": "ticket not found"}

        requester = db.get(User, ticket.user_id)
        settings_row = db.execute(select(SiteSettings).limit(1)).scalar_one_or_none()
        contact_email = settings_row.contact_email if settings_row else None

        if not contact_email:
            logger.warning("notifications.support_ticket_no_contact_email", ticket_id=ticket_id)
            return {"status": "skipped", "reason": "no contact email configured"}

        try:
            email_service.send_support_ticket_created_email(
                contact_email,
                subject=ticket.subject,
                requester_name=requester.full_name if requester else "A user",
                requester_email=requester.email if requester else "unknown",
                message=ticket.message,
            )
        except Exception:
            logger.exception("notifications.email_send_failed", to=contact_email)

        return {"status": "complete"}


@celery_app.task(name="notifications.process_pending_deletions", bind=True, max_retries=2)
def process_pending_deletions(self) -> dict:
    """Daily scan (Celery beat, Master Plan §3 Phase 6 task 3) — revokes
    active grants for users whose 30-day deletion grace period has elapsed.
    Does NOT delete the user row itself; account/row deletion or
    anonymization is intentionally left to a later phase."""
    cutoff = datetime.now(UTC) - timedelta(days=_DELETION_GRACE_PERIOD_DAYS)

    with get_sync_db() as db:
        result = db.execute(
            select(User).where(
                User.deletion_requested_at.is_not(None), User.deletion_requested_at <= cutoff
            )
        )
        users = list(result.scalars().all())

        revoked_count = 0
        for user in users:
            grants_result = db.execute(
                select(AccessGrant).where(
                    AccessGrant.user_id == user.id, AccessGrant.status == GrantStatus.ACTIVE.value
                )
            )
            for grant in grants_result.scalars().all():
                grant.status = GrantStatus.REVOKED.value
                user.datasets_granted = max(0, (user.datasets_granted or 0) - 1)

                in_flight = db.execute(
                    select(SubsetExtraction).where(
                        SubsetExtraction.grant_id == grant.id,
                        SubsetExtraction.status.in_(
                            [ExtractionStatus.QUEUED.value, ExtractionStatus.PROCESSING.value]
                        ),
                    )
                )
                for extraction in in_flight.scalars().all():
                    extraction.status = ExtractionStatus.FAILED.value
                    extraction.error_message = "Grant revoked: account deletion grace period elapsed."

                revoked_count += 1

        db.commit()
        return {"status": "complete", "users_processed": len(users), "grants_revoked": revoked_count}
