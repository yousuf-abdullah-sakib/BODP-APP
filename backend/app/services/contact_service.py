from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifications import ContactSubmission, ContactSubmissionStatus
from app.schemas.admin_contact import ContactSubmissionCreate
from app.services.admin_notify_service import notify_admins_with_permission


async def create_submission(
    db: AsyncSession, data: ContactSubmissionCreate
) -> ContactSubmission:
    submission = ContactSubmission(
        name=data.name.strip(),
        email=str(data.email).strip().lower(),
        organization=(data.organization or "").strip() or None,
        subject=data.subject,
        message=data.message.strip(),
        status=ContactSubmissionStatus.NEW.value,
    )
    db.add(submission)
    await db.flush()

    # In-app notification for admins who can actually act on this (hold
    # "Manage Support") — same pattern as every other "something needs
    # admin attention" event, so this actually surfaces in the admin bell/
    # notifications list instead of only existing as a DB row nobody sees.
    await notify_admins_with_permission(
        db,
        permission="Manage Support",
        type="info",
        title="New contact form submission",
        description=f"{submission.name} ({submission.email}) sent a message: \"{submission.subject}\".",
    )

    await db.commit()
    await db.refresh(submission)
    return submission
