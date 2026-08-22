"""Creates (or re-verifies) a single dedicated, already-verified user for
backend/scripts/load_test.py to log in as (Phase 10 hardening task 3).

Deliberately separate from load_test.py itself: that tool is a pure HTTP
client hitting the already-running API from outside, mirroring a real
browser/script — it authenticates via the ordinary POST /auth/login flow,
never touching the database directly. Bootstrapping a *verified* account
needs one direct DB write somewhere (normal registration requires a real
emailed verification link), and this script is that one place, run once
before a load-test session, not part of the load-generation path itself.

Usage:
    python -m app.scripts.seed_load_test_user [--email EMAIL] [--password PASSWORD]

Idempotent — safe to re-run; if the user already exists it's left as-is
(password/verification are only set on first creation).
"""

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.user import User, UserStatus

_DEFAULT_EMAIL = "load-test@example.com"
_DEFAULT_PASSWORD = "LoadTest123x"


async def ensure_load_test_user(email: str, password: str) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None:
            return {"email": email, "created": False, "id": str(user.id)}

        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name="Load Test User",
            role="user",
            status=UserStatus.ACTIVE.value,
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return {"email": email, "created": True, "id": str(user.id)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=_DEFAULT_EMAIL)
    parser.add_argument("--password", default=_DEFAULT_PASSWORD)
    args = parser.parse_args()

    result = asyncio.run(ensure_load_test_user(args.email, args.password))
    for key, value in result.items():
        print(f"{key}: {value}")
