import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_email_verification_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.audit import ActiveSession, FailedLogin
from app.models.user import User, UserRoleEnum, UserStatus
from app.services import settings_service
from app.services.admin_notify_service import notify_admins
from app.services.email_service import email_service


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AuthService:
    async def register(
        self,
        db: AsyncSession,
        *,
        full_name: str,
        email: str,
        password: str,
        institution: str | None,
        phone: str | None,
    ) -> User:
        normalized_email = email.strip().lower()
        existing = await db.execute(select(User).where(User.email == normalized_email))
        if existing.scalar_one_or_none() is not None:
            raise AuthError("An account with this email already exists", status_code=409)

        user = User(
            email=normalized_email,
            password_hash=hash_password(password),
            full_name=full_name.strip(),
            institution=institution,
            phone=phone,
            role=UserRoleEnum.USER.value,
            status=UserStatus.ACTIVE.value,
        )
        db.add(user)
        await db.flush()

        await notify_admins(
            db,
            type="info",
            title="New user registered",
            description=f"{user.full_name} ({user.email}) created an account.",
        )

        await db.commit()
        await db.refresh(user)

        token = create_email_verification_token(str(user.id), user.email)
        email_service.send_verification_email(user.email, user.full_name, token)

        return user

    async def verify_email(self, db: AsyncSession, token: str) -> User:
        try:
            payload = decode_token(token, expected_type=TokenType.EMAIL_VERIFY)
        except InvalidTokenError as exc:
            raise AuthError("Verification link is invalid or has expired", status_code=400) from exc

        user_id = uuid.UUID(payload["sub"])
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise AuthError("Account not found", status_code=404)

        if user.email_verified_at is not None:
            return user

        user.email_verified_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
        return user

    async def resend_verification(self, db: AsyncSession, email: str) -> None:
        result = await db.execute(select(User).where(User.email == email.strip().lower()))
        user = result.scalar_one_or_none()
        # Intentionally silent on unknown email / already-verified — do not
        # leak account existence to an unauthenticated caller.
        if user is None or user.email_verified_at is not None:
            return
        token = create_email_verification_token(str(user.id), user.email)
        email_service.send_verification_email(user.email, user.full_name, token)

    async def authenticate(
        self,
        db: AsyncSession,
        *,
        email: str,
        password: str,
        ip_address: str | None,
    ) -> User:
        normalized_email = email.strip().lower()

        # Phase 10 security audit finding: this check must run BEFORE
        # password verification, not after. The previous ordering placed
        # it after the wrong-password branch's `raise`, making it
        # unreachable from the exact scenario it exists to defend against
        # — a brute-force attacker guessing passwords never got far
        # enough to hit this check, since every wrong guess already
        # raised 401 first. Checking lockout status up front also means
        # a correct password can't bypass an active lockout window either
        # (the previous ordering would have let the attacker's lucky
        # guess straight through), which is the correct behavior for a
        # real lockout control.
        if await self._is_locked_out(db, normalized_email):
            raise AuthError(
                "Too many failed login attempts. Please try again later.", status_code=429
            )

        result = await db.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()

        if user is None or not verify_password(password, user.password_hash):
            await self._record_failed_login(db, normalized_email, ip_address, "invalid_credentials")
            raise AuthError("Invalid email or password", status_code=401)

        if user.status == UserStatus.SUSPENDED.value:
            await self._record_failed_login(db, normalized_email, ip_address, "account_suspended")
            raise AuthError("This account has been suspended", status_code=403)

        if user.email_verified_at is None:
            raise AuthError("Please verify your email address before logging in", status_code=403)

        return user

    async def _record_failed_login(
        self, db: AsyncSession, email: str, ip_address: str | None, reason: str
    ) -> None:
        db.add(FailedLogin(email_attempted=email, ip_address=ip_address, reason=reason))
        await db.commit()

    async def _is_locked_out(self, db: AsyncSession, email: str) -> bool:
        from datetime import timedelta

        from sqlalchemy import func

        window_start = datetime.now(timezone.utc) - timedelta(
            minutes=settings.FAILED_LOGIN_LOCKOUT_WINDOW_MINUTES
        )
        result = await db.execute(
            select(func.count(FailedLogin.id)).where(
                FailedLogin.email_attempted == email,
                FailedLogin.created_at >= window_start,
            )
        )
        count = result.scalar_one()
        return count >= settings.FAILED_LOGIN_LOCKOUT_THRESHOLD

    async def issue_tokens(
        self,
        db: AsyncSession,
        user: User,
        *,
        device: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[str, str]:
        session_id = str(uuid.uuid4())
        expire_minutes = await settings_service.get_session_lifetime_minutes(db)
        access_token = create_access_token(
            str(user.id), user.role, session_id, expire_minutes=expire_minutes
        )
        refresh_token = create_refresh_token(str(user.id), session_id)

        refresh_payload = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        db.add(
            ActiveSession(
                # Use the JWT `sid` claim as the row's own PK rather than
                # letting UUIDPKMixin generate a random one — this is what
                # lets /me/sessions correlate "which row is my current
                # session" back to the sid claim on the caller's own access
                # token (see core/deps.py's get_current_user, which stashes
                # the decoded sid on request.state for exactly this).
                id=uuid.UUID(session_id),
                user_id=user.id,
                refresh_token_jti=refresh_payload["jti"],
                device=device,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )
        await db.commit()
        return access_token, refresh_token

    async def refresh_access_token(self, db: AsyncSession, refresh_token: str) -> tuple[str, str]:
        try:
            payload = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        except InvalidTokenError as exc:
            raise AuthError("Refresh token is invalid or expired", status_code=401) from exc

        result = await db.execute(
            select(ActiveSession).where(ActiveSession.refresh_token_jti == payload["jti"])
        )
        session = result.scalar_one_or_none()
        if session is None or session.is_revoked:
            raise AuthError("Session has been revoked", status_code=401)

        user_id = uuid.UUID(payload["sub"])
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if user is None or user.status == UserStatus.SUSPENDED.value:
            raise AuthError("Account is not available", status_code=403)

        session.last_active_at = datetime.now(timezone.utc)
        await db.commit()

        expire_minutes = await settings_service.get_session_lifetime_minutes(db)
        new_access_token = create_access_token(
            str(user.id), user.role, payload["sid"], expire_minutes=expire_minutes
        )
        return new_access_token, refresh_token

    async def logout(self, db: AsyncSession, refresh_token: str) -> None:
        try:
            payload = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        except InvalidTokenError:
            return

        result = await db.execute(
            select(ActiveSession).where(ActiveSession.refresh_token_jti == payload["jti"])
        )
        session = result.scalar_one_or_none()
        if session is not None and not session.is_revoked:
            session.revoked_at = datetime.now(timezone.utc)
            await db.commit()

    async def request_password_reset(self, db: AsyncSession, email: str) -> None:
        result = await db.execute(select(User).where(User.email == email.strip().lower()))
        user = result.scalar_one_or_none()
        if user is None:
            return
        token = create_password_reset_token(str(user.id), user.email)
        email_service.send_password_reset_email(user.email, user.full_name, token)

    async def confirm_password_reset(self, db: AsyncSession, token: str, new_password: str) -> None:
        try:
            payload = decode_token(token, expected_type=TokenType.PASSWORD_RESET)
        except InvalidTokenError as exc:
            raise AuthError("Reset link is invalid or has expired", status_code=400) from exc

        user_id = uuid.UUID(payload["sub"])
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise AuthError("Account not found", status_code=404)

        user.password_hash = hash_password(new_password)
        await db.commit()

        # Revoke all existing sessions on password reset — a compromised
        # credential shouldn't leave old sessions valid.
        await self._revoke_all_sessions(db, user.id)

    async def confirm_invite(self, db: AsyncSession, token: str, new_password: str) -> User:
        try:
            payload = decode_token(token, expected_type=TokenType.INVITE)
        except InvalidTokenError as exc:
            raise AuthError("Invite link is invalid or has expired", status_code=400) from exc

        user_id = uuid.UUID(payload["sub"])
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise AuthError("Account not found", status_code=404)

        user.password_hash = hash_password(new_password)
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(timezone.utc)
        await db.commit()
        return user

    async def change_password(
        self, db: AsyncSession, user: User, current_password: str, new_password: str
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise AuthError("Current password is incorrect", status_code=400)
        user.password_hash = hash_password(new_password)
        await db.commit()
        await self._revoke_all_sessions(db, user.id)

    async def _revoke_all_sessions(self, db: AsyncSession, user_id: uuid.UUID) -> None:
        result = await db.execute(
            select(ActiveSession).where(
                ActiveSession.user_id == user_id, ActiveSession.revoked_at.is_(None)
            )
        )
        for session in result.scalars().all():
            session.revoked_at = datetime.now(timezone.utc)
        await db.commit()


auth_service = AuthService()
