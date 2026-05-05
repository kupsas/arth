"""
Structured API errors for Arth.

Routes raise :class:`ArthError` instead of generic ``HTTPException`` when they want a
stable ``error_code`` the dashboard can branch on. The JSON body shape is always::

    {"detail": {"error_code": "<CODE>", "message": "<user text>", "hint": "<optional>"}}

Plain-string ``HTTPException`` responses remain valid for backward compatibility.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Payload nested under FastAPI's ``detail`` field for :class:`ArthError`."""

    error_code: str = Field(..., description="Stable machine-readable code")
    message: str = Field(..., description="User-facing explanation")
    hint: str | None = Field(None, description="Optional next step")


class ArthError(HTTPException):
    """HTTP exception whose ``detail`` is a dict matching :class:`ErrorResponse`."""

    def __init__(
        self,
        *,
        code: str,
        status_code: int,
        message: str,
        hint: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.error_code = code
        # Avoid shadowing HTTPException.detail; expose readable names for handlers/logging.
        self.message_text = message
        self.hint_text = hint
        payload = ErrorResponse(error_code=code, message=message, hint=hint)
        super().__init__(
            status_code=status_code,
            detail=payload.model_dump(exclude_none=False),
            headers=headers,
        )


# --- Stable codes (use these strings everywhere; typos break clients) -----------------


class ErrorCodes:
    """Application-level error_code values returned in API JSON."""

    # Auth
    AUTH_SESSION_EXPIRED = "AUTH_SESSION_EXPIRED"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"

    # Gmail / scraper
    GMAIL_NOT_CONNECTED = "GMAIL_NOT_CONNECTED"
    GMAIL_REAUTH_REQUIRED = "GMAIL_REAUTH_REQUIRED"
    GMAIL_OAUTH_IN_PROGRESS = "GMAIL_OAUTH_IN_PROGRESS"
    SCRAPER_CREDENTIALS_MISSING = "SCRAPER_CREDENTIALS_MISSING"

    # Pipeline / uploads
    PARSER_FAILED = "PARSER_FAILED"
    PARSER_UNSUPPORTED_FORMAT = "PARSER_UNSUPPORTED_FORMAT"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    UPLOAD_NEEDS_PASSWORD = "UPLOAD_NEEDS_PASSWORD"
    UPLOAD_WRONG_PASSWORD = "UPLOAD_WRONG_PASSWORD"

    # Generic resources
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"

    # Validation (bad input, rule violations — prefer specific message in ``message``)
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # Goals
    GOAL_NOT_FOUND = "GOAL_NOT_FOUND"
    GOAL_ALLOCATION_FAILED = "GOAL_ALLOCATION_FAILED"

    # Catch-all
    INTERNAL_ERROR = "INTERNAL_ERROR"


# --- Factories (copy-friendly defaults; routes may still pass custom message/hint) ---


def arth_auth_session_expired(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.AUTH_SESSION_EXPIRED,
        status_code=401,
        message="Your session expired. Sign in again.",
        hint=hint,
    )


def arth_auth_forbidden(message: str | None = None, hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.AUTH_FORBIDDEN,
        status_code=403,
        message=message or "You don't have access to that.",
        hint=hint,
    )


def arth_gmail_not_connected(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.GMAIL_NOT_CONNECTED,
        status_code=503,
        message="Connect Gmail in Settings before importing from email.",
        hint=hint,
    )


def arth_gmail_reauth_required(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.GMAIL_REAUTH_REQUIRED,
        status_code=503,
        message="Gmail needs to be reconnected — your previous login expired.",
        hint=hint or "Open Settings and connect Gmail again.",
    )


def arth_gmail_oauth_in_progress(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.GMAIL_OAUTH_IN_PROGRESS,
        status_code=409,
        message="Gmail sign-in is already in progress. Finish or cancel that window first.",
        hint=hint,
    )


def arth_scraper_credentials_missing(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.SCRAPER_CREDENTIALS_MISSING,
        status_code=503,
        message="Email import isn't set up — Gmail credentials are missing on the server.",
        hint=hint,
    )


def arth_parser_failed(message: str, hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.PARSER_FAILED,
        status_code=400,
        message=message,
        hint=hint,
    )


def arth_parser_unsupported_format(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.PARSER_UNSUPPORTED_FORMAT,
        status_code=400,
        message="That file format isn't supported for this upload.",
        hint=hint,
    )


def arth_upload_too_large(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.UPLOAD_TOO_LARGE,
        status_code=413,
        message="That file is too large to upload.",
        hint=hint or "Try a smaller export or split the file.",
    )


def arth_upload_needs_password(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.UPLOAD_NEEDS_PASSWORD,
        status_code=400,
        message="This PDF is password-protected.",
        hint=hint or "Enter the password you use to open the statement.",
    )


def arth_upload_wrong_password(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.UPLOAD_WRONG_PASSWORD,
        status_code=400,
        message="That password didn't unlock the PDF.",
        hint=hint or "Double-check the password and try again.",
    )


def arth_resource_not_found(resource: str = "That item", hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.RESOURCE_NOT_FOUND,
        status_code=404,
        message=f"{resource} wasn't found.",
        hint=hint,
    )


def arth_resource_conflict(message: str, hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.RESOURCE_CONFLICT,
        status_code=409,
        message=message,
        hint=hint,
    )


def arth_validation_error(message: str, hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.VALIDATION_ERROR,
        status_code=400,
        message=message,
        hint=hint,
    )


def arth_goal_not_found(hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.GOAL_NOT_FOUND,
        status_code=404,
        message="That goal wasn't found.",
        hint=hint,
    )


def arth_goal_allocation_failed(message: str, hint: str | None = None) -> ArthError:
    return ArthError(
        code=ErrorCodes.GOAL_ALLOCATION_FAILED,
        status_code=500,
        message=message,
        hint=hint,
    )


def arth_internal_error(
    message: str | None = None,
    hint: str | None = None,
) -> ArthError:
    return ArthError(
        code=ErrorCodes.INTERNAL_ERROR,
        status_code=500,
        message=message or "Something unexpected happened.",
        hint=hint or "If it keeps happening, download logs from Settings and share them when reporting the issue.",
    )


def is_arth_error_detail(detail: Any) -> bool:
    """Return True if ``detail`` looks like an :class:`ArthError` payload."""
    return (
        isinstance(detail, dict)
        and "error_code" in detail
        and "message" in detail
        and isinstance(detail.get("error_code"), str)
        and isinstance(detail.get("message"), str)
    )
