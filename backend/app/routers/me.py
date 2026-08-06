import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest
from app.schemas.me import (
    AvatarUploadResponse,
    CmsBlockSummary,
    DeletionStatusResponse,
    NotificationSummary,
    OverviewResponse,
    PreferencesSchema,
    ProfileDetail,
    ProfileUpdate,
    SessionSummary,
    SupportTicketCreate,
    SupportTicketSummary,
    UnreadCountResponse,
)
from app.services import (
    cms_service,
    notification_service,
    overview_service,
    preferences_service,
    profile_service,
    security_service,
    support_service,
)
from app.services.auth_service import AuthError, auth_service

router = APIRouter(prefix="/me", tags=["me"])


# --- Overview ---


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await overview_service.get_overview(db, current_user)


# --- Profile ---


@router.get("/profile", response_model=ProfileDetail)
async def get_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/profile", response_model=ProfileDetail)
async def update_profile(
    body: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = profile_service.update_profile(current_user, body)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/profile/avatar", response_model=AvatarUploadResponse)
async def upload_avatar(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    key = await profile_service.upload_avatar(db, current_user, filename=file.filename, file_stream=file)
    return AvatarUploadResponse(avatar_key=key)


# --- Security ---


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revokes all active sessions (including the caller's own) on success —
    the frontend should warn the user before submitting and expect to be
    signed out."""
    try:
        await auth_service.change_password(
            db, current_user, body.current_password, body.new_password
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_session_id = getattr(request.state, "session_id", None)
    current_session_uuid = uuid.UUID(current_session_id) if current_session_id else None
    pairs = await security_service.list_sessions(
        db, current_user.id, current_session_id=current_session_uuid
    )
    return [
        SessionSummary(
            id=s.id,
            device=s.device,
            ip_address=s.ip_address,
            location=s.location,
            user_agent=s.user_agent,
            created_at=s.created_at,
            last_active_at=s.last_active_at,
            is_current=is_current,
        )
        for s, is_current in pairs
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await security_service.revoke_session(db, current_user.id, session_id)


@router.post("/request-deletion", response_model=DeletionStatusResponse)
async def request_deletion(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    requested_at = await profile_service.request_deletion(db, current_user)
    return DeletionStatusResponse(deletion_requested_at=requested_at)


@router.post("/cancel-deletion", response_model=DeletionStatusResponse)
async def cancel_deletion(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await profile_service.cancel_deletion(db, current_user)
    return DeletionStatusResponse(deletion_requested_at=None)


# --- Preferences ---


@router.get("/preferences", response_model=PreferencesSchema)
async def get_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await preferences_service.get_preferences(db, current_user.id)


@router.patch("/preferences", response_model=PreferencesSchema)
async def update_preferences(
    body: PreferencesSchema,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await preferences_service.update_preferences(db, current_user.id, body)


# --- Notifications ---


@router.get("/notifications", response_model=list[NotificationSummary])
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await notification_service.list_notifications(db, current_user.id)


@router.get("/notifications/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count = await notification_service.unread_count(db, current_user.id)
    return UnreadCountResponse(unread_count=count)


@router.patch("/notifications/{notification_id}/read", response_model=NotificationSummary)
async def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await notification_service.mark_read(db, current_user.id, notification_id)


@router.post("/notifications/read-all", status_code=204)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await notification_service.mark_all_read(db, current_user.id)


# --- Support tickets ---


@router.post("/support-tickets", response_model=SupportTicketSummary, status_code=201)
async def create_support_ticket(
    body: SupportTicketCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await support_service.create_ticket(db, current_user, body)


@router.get("/support-tickets", response_model=list[SupportTicketSummary])
async def list_support_tickets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await support_service.list_tickets_for_user(db, current_user.id)


# --- CMS content ---


@router.get("/cms-blocks", response_model=list[CmsBlockSummary])
async def get_cms_blocks(
    page: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    blocks = await cms_service.get_blocks(db, page)
    return [CmsBlockSummary(**b) for b in blocks]
