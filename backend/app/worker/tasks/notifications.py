import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.models.catalog import Dataset
from app.models.notifications import Notification, NotificationType
from app.models.requests import AccessGrant, DatasetRequest, GrantStatus
from app.models.user import Role, User, UserRole
from app.services.email_service import email_service
from app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)

_EXPIRING_SOON_DAYS = 7


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
