from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_client_ip, get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    TokenPair,
    UserPublic,
    VerifyEmailRequest,
)
from app.services.auth_service import AuthError, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _raise(exc: AuthError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def register(request: Request, payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    try:
        user = await auth_service.register(
            db,
            full_name=payload.full_name,
            email=payload.email,
            password=payload.password,
            institution=payload.institution,
            phone=payload.phone,
        )
    except AuthError as exc:
        _raise(exc)
    return RegisterResponse(id=user.id, email=user.email)


@router.post("/verify-email", response_model=UserPublic)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def verify_email(
    request: Request, payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)
):
    try:
        user = await auth_service.verify_email(db, payload.token)
    except AuthError as exc:
        _raise(exc)
    return user


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def resend_verification(
    request: Request, payload: ResendVerificationRequest, db: AsyncSession = Depends(get_db)
):
    await auth_service.resend_verification(db, payload.email)
    return {"message": "If an account exists for this email, a verification link has been sent."}


@router.post("/login", response_model=LoginResponse)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    ip_address = get_client_ip(request)
    try:
        user = await auth_service.authenticate(
            db, email=payload.email, password=payload.password, ip_address=ip_address
        )
    except AuthError as exc:
        _raise(exc)

    access_token, refresh_token = await auth_service.issue_tokens(
        db,
        user,
        device=request.headers.get("user-agent"),
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
    )

    return LoginResponse(
        user=UserPublic.model_validate(user),
        tokens=TokenPair(access_token=access_token, refresh_token=refresh_token),
    )


@router.post("/refresh", response_model=TokenPair)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def refresh(request: Request, payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        access_token, refresh_token = await auth_service.refresh_access_token(
            db, payload.refresh_token
        )
    except AuthError as exc:
        _raise(exc)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: LogoutRequest, db: AsyncSession = Depends(get_db)):
    await auth_service.logout(db, payload.refresh_token)
    return None


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def request_password_reset(
    request: Request, payload: PasswordResetRequest, db: AsyncSession = Depends(get_db)
):
    await auth_service.request_password_reset(db, payload.email)
    return {"message": "If an account exists for this email, a password reset link has been sent."}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def confirm_password_reset(
    request: Request, payload: PasswordResetConfirm, db: AsyncSession = Depends(get_db)
):
    try:
        await auth_service.confirm_password_reset(db, payload.token, payload.new_password)
    except AuthError as exc:
        _raise(exc)
    return None


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await auth_service.change_password(
            db, current_user, payload.current_password, payload.new_password
        )
    except AuthError as exc:
        _raise(exc)
    return None
