"""
Authentication routes — login, logout, session status.

POST /api/auth/login   → verify credentials, set session cookie
POST /api/auth/logout  → clear session cookie
GET  /api/auth/me      → return current user info (requires valid session)

Cookie attributes:
  - httponly=True   — JavaScript cannot read it (XSS protection)
  - samesite="lax"  — sent on top-level navigations but not cross-site POSTs
                      (CSRF protection for same-origin use)
  - path="/"        — valid for all paths
  - secure=False    — localhost only; set to True if serving over HTTPS
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from api.auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_token,
    get_current_user,
    verify_credentials,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class AuthStatusResponse(BaseModel):
    authenticated: bool
    username: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/login", response_model=AuthStatusResponse)
def login(body: LoginRequest, response: Response) -> AuthStatusResponse:
    """Verify credentials and issue a session cookie.

    On success: sets an httpOnly "arth_session" cookie and returns 200.
    On failure: returns 401 — same error for wrong username OR wrong password
    (no information leakage about which field was wrong).
    """
    if not verify_credentials(body.username, body.password):
        logger.warning("Failed login attempt for username: %r", body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Hmm, wrong username or password. Give it another shot.",
        )

    token = create_session_token(body.username)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,       # not readable by JavaScript
        samesite="lax",      # safe for same-site cross-port (localhost:3000 → localhost:8000)
        secure=False,        # localhost only; flip to True for HTTPS
        path="/",
    )
    logger.info("Successful login for user: %r", body.username)
    return AuthStatusResponse(authenticated=True, username=body.username)


@router.post("/logout", response_model=AuthStatusResponse)
def logout(response: Response) -> AuthStatusResponse:
    """Clear the session cookie, effectively logging the user out."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return AuthStatusResponse(authenticated=False)


@router.get("/me", response_model=AuthStatusResponse)
def me(username: str = Depends(get_current_user)) -> AuthStatusResponse:
    """Return the current session's username.

    The dashboard calls this on load to check if the session is still valid
    without decoding the cookie client-side (it's httpOnly — JS can't read it).
    Returns 401 if the session is missing or expired.
    """
    return AuthStatusResponse(authenticated=True, username=username)
