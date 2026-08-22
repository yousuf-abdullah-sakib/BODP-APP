import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import InvalidTokenError, TokenType, decode_token
from app.models.user import User, UserStatus

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        ) from exc

    try:
        result = await db.execute(select(User).where(User.id == user_id))
    except Exception as exc:
        # Phase 10.4 finding: every authenticated endpoint in the app
        # 500s when Postgres is unreachable, since this DB round trip
        # (needed to load the real User row a valid JWT points at) had
        # no error handling at all — confirmed live by testing the new
        # admin/health endpoint with Postgres stopped, which is supposed
        # to degrade gracefully but never got the chance to, since this
        # dependency failed before the endpoint's own code ever ran. A
        # real 503 with a clear message is the correct response here —
        # the token itself may well be valid, the backing store just
        # isn't reachable right now, which is a different failure mode
        # than "not authenticated" (401) and should not be reported as
        # one.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable — please try again shortly.",
        ) from exc
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if user.status == UserStatus.SUSPENDED.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is suspended")

    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Email address not verified"
        )

    request.state.user = user
    # The `sid` claim doubles as the caller's ActiveSession.id (see
    # auth_service.issue_tokens) — stashed here so /me/sessions can flag
    # "this is your current session" without re-decoding the token itself.
    request.state.session_id = payload.get("sid")
    return user


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Same as get_current_user but returns None instead of 401ing when no
    (or an invalid) token is presented — for endpoints that serve both
    anonymous and logged-in callers but behave differently for each, e.g.
    catalog detail views only logging a DatasetView row when someone is
    actually logged in."""
    if credentials is None:
        return None
    try:
        return await get_current_user(request, credentials, db)
    except HTTPException:
        return None


def get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None
