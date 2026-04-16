"""
Authentication utilities for the Arth API.

Strategy: cookie-based sessions, single shared password.

Flow:
  1. POST /api/auth/login  → verify username + bcrypt password
                           → create a signed token (itsdangerous)
                           → set it as an httpOnly cookie named "arth_session"
  2. Every protected endpoint uses Depends(get_current_user)
     → reads "arth_session" cookie → verifies signature + expiry
     → returns the username, or raises HTTP 401
  3. POST /api/auth/logout → clears the cookie

Password storage:
  AUTH_PASSWORD is loaded from .env as plaintext, then hashed with bcrypt once
  at module import time.  The hash lives in memory only — never written to disk.
  This means the password can be changed by editing .env and restarting.

Session tokens:
  itsdangerous.URLSafeTimedSerializer signs a JSON payload with AUTH_SECRET_KEY.
  Tokens expire after SESSION_MAX_AGE seconds (default: 30 days).
  They are NOT JWT — they can't be inspected without the secret key.
"""

from __future__ import annotations

import logging
import os

import bcrypt
from dotenv import load_dotenv
from fastapi import Cookie, Depends, HTTPException, Query, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — read once at startup
# ---------------------------------------------------------------------------

COOKIE_NAME = "arth_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days in seconds

_AUTH_USERNAME: str = os.getenv("AUTH_USERNAME", "sashank")
_AUTH_PASSWORD_PLAIN: str = os.getenv("AUTH_PASSWORD", "")
_AUTH_SECRET_KEY: str = os.getenv("AUTH_SECRET_KEY", "")

if not _AUTH_PASSWORD_PLAIN:
    logger.warning("AUTH_PASSWORD is not set in .env — login will always fail")

if not _AUTH_SECRET_KEY:
    logger.warning(
        "AUTH_SECRET_KEY is not set in .env — using a random key. "
        "All sessions will be invalidated on every restart."
    )
    import secrets
    _AUTH_SECRET_KEY = secrets.token_hex(32)

# Hash the password once at module load time (bcrypt is slow by design — ~100ms).
# We never store the hash anywhere; it lives in memory for the lifetime of the process.
_PASSWORD_HASH: bytes = bcrypt.hashpw(
    _AUTH_PASSWORD_PLAIN.encode("utf-8"),
    bcrypt.gensalt(rounds=12),
)

# The serializer signs/verifies tokens using the secret key.
_serializer = URLSafeTimedSerializer(_AUTH_SECRET_KEY)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def verify_credentials(username: str, password: str) -> bool:
    """Return True if the username + password match the configured credentials.

    Uses bcrypt.checkpw which is timing-safe (constant time comparison).
    We check username first with a plain equality check; if that fails we still
    run bcrypt so the timing doesn't reveal whether the username was correct.
    """
    username_ok = username == _AUTH_USERNAME
    # Always run bcrypt even if username is wrong — avoids timing side-channel.
    password_ok = bcrypt.checkpw(password.encode("utf-8"), _PASSWORD_HASH)
    return username_ok and password_ok


def create_session_token() -> str:
    """Create a signed, time-limited session token containing the username."""
    return _serializer.dumps(_AUTH_USERNAME)


def verify_session_token(token: str) -> str:
    """Verify the token signature and expiry. Returns the username on success.

    Raises:
        HTTPException(401) if the token is invalid or expired.
    """
    try:
        username: str = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return username
    except SignatureExpired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired — please log in again",
        )
    except BadSignature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token",
        )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

def get_current_user(
    arth_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> str:
    """FastAPI dependency that enforces authentication.

    Usage:
        @router.get("/protected")
        def protected_endpoint(user: str = Depends(get_current_user)):
            ...

    Or on the whole router:
        app.include_router(router, dependencies=[Depends(get_current_user)])

    Returns the authenticated username. Raises HTTP 401 if not authenticated.
    """
    if arth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return verify_session_token(arth_session)


def effective_user_id(
    user_id: str | None = Query(
        None,
        description="Optional; when set must match the logged-in user (same string as username).",
    ),
    current_user: str = Depends(get_current_user),
) -> str:
    """Resolve the Arth user id for data-scoped endpoints (session wins; param is checked)."""
    out = (user_id or "").strip() or current_user
    if out != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_id must match the authenticated user.",
        )
    return out
