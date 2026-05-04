"""
Central mapping of bank PDF **logical kinds** → environment variable chains.

Parsers call :func:`resolve_pdf_password_chain` instead of repeating
``os.getenv("A") or os.getenv("B")``. Chains go through
:func:`scraper.secrets_context.resolve_secret_env` so the setup wizard can store
values in ``UserSecrets`` (encrypted) with the same keys as ``.env``.

When those are empty, optional ``parser_key=…`` looks up :class:`~api.models.PasswordTemplate`
and builds a password from encrypted **ingredients** (PAN, DOB, account fragments)
the user saved during onboarding (``ARTH_PDF_INGREDIENT_*`` keys in ``UserSecrets``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pikepdf
from sqlmodel import Session, select

from scraper.secrets_context import get_statement_secrets_scope, resolve_secret_env

logger = logging.getLogger(__name__)

# Primary key first, then legacy / alternate names (same order everywhere).
HDFC_COMBINED_STATEMENT_PASSWORD_KEYS = ("HDFC_STATEMENT_PASSWORD",)
HDFC_CC_STATEMENT_PASSWORD_KEYS = ("HDFC_CC_STATEMENT_PASSWORD", "HDFC_STATEMENT_PASSWORD")
ICICI_MONTHLY_STATEMENT_PASSWORD_KEYS = ("ICICI_STATEMENT_MONTHLY_PASSWORD", "ICICI_STATEMENT_PASSWORD")
ICICI_ANNUAL_STATEMENT_PASSWORD_KEYS = ("ICICI_STATEMENT_ANNUAL_PASSWORD",)
ICICI_DIRECT_TRADE_PASSWORD_KEYS = ("ICICI_DIRECT_EMAIL_PASSWORD", "ICICI_DIRECT_TRADE_PASSWORD")
NSE_TRADES_EXECUTED_PASSWORD_KEYS = ("NSE_TRADES_EXECUTED_PASSWORD", "ICICI_DIRECT_TRADE_PASSWORD")
# ICICI Direct / ICICI Sec equity + MF **statement** PDFs from email (Phase WS1).
ICICI_DIRECT_STATEMENT_PASSWORD_KEYS = (
    "ICICI_STATEMENT_MONTHLY_PASSWORD",
    "ICICI_DIRECT_EMAIL_PASSWORD",
)

# Stored in UserSecrets.secrets_json — onboarding form merges these alongside env-key mirrors.
ARTH_PDF_INGREDIENT_PAN = "ARTH_PDF_INGREDIENT_PAN"
ARTH_PDF_INGREDIENT_DOB_ISO = "ARTH_PDF_INGREDIENT_DOB_ISO"
ARTH_PDF_INGREDIENT_HDFC_ACCOUNT_NUMBER = "ARTH_PDF_INGREDIENT_HDFC_ACCOUNT_NUMBER"
ARTH_PDF_INGREDIENT_HDFC_CC_LAST4 = "ARTH_PDF_INGREDIENT_HDFC_CC_LAST4"

# ``scraper.config.BANK_SENDERS`` ``parser_key`` → :class:`~api.models.PasswordTemplate.parser_key` rows.
EMAIL_PARSER_KEY_TO_PASSWORD_TEMPLATE_KEYS: dict[str, tuple[str, ...]] = {
    "icici_statement": ("icici_statement_monthly", "icici_statement_annual"),
    "hdfc_combined_statement": ("hdfc_combined_statement",),
    "hdfc_cc_statement": ("hdfc_cc_statement",),
    # NSE + ICICI Direct trade PDFs both use PAN-style secrets; list both template rows.
    "icici_direct_trade": ("nse_trades_executed", "icici_direct_trade"),
    # ICICI Securities quarterly equity/MF statement PDFs (same password family as Direct).
    "icici_direct_statement": ("icici_direct_trade",),
}


class StatementPasswordRequired(Exception):
    """Raised when a statement PDF cannot be opened because no password was resolved.

    ``parser_key`` identifies the :class:`~api.models.PasswordTemplate` row (if any)
    so onboarding can prompt for the right ingredients.
    """

    def __init__(self, parser_key: str, detail: str = "") -> None:
        self.parser_key = parser_key
        msg = detail or (
            f"Add bank PDF password ingredients or set the matching env/UserSecrets key ({parser_key})."
        )
        super().__init__(msg)


def _dob_iso_to_ddmmyyyy(iso: str) -> str:
    """Turn ``YYYY-MM-DD`` into ``DDMMYYYY`` (common Indian bank PDF pattern)."""
    raw = (iso or "").strip()[:10]
    parts = raw.split("-")
    if len(parts) != 3:
        return ""
    try:
        y, m, d = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return ""
    if y < 1900 or m < 1 or m > 12 or d < 1 or d > 31:
        return ""
    return f"{d:02d}{m:02d}{y}"


def _logical_kwargs_from_user_secrets(secrets: dict[str, Any]) -> dict[str, str]:
    """Map PasswordTemplate placeholder names → values from decrypted JSON."""
    pan = str(secrets.get(ARTH_PDF_INGREDIENT_PAN, "")).strip().upper()
    dob_iso = str(secrets.get(ARTH_PDF_INGREDIENT_DOB_ISO, "")).strip()
    hdfc_acct = str(secrets.get(ARTH_PDF_INGREDIENT_HDFC_ACCOUNT_NUMBER, "")).strip()
    hdfc_cc4 = str(secrets.get(ARTH_PDF_INGREDIENT_HDFC_CC_LAST4, "")).strip()
    return {
        "pan": pan,
        "dob_ddmmyyyy": _dob_iso_to_ddmmyyyy(dob_iso),
        "hdfc_account_number": hdfc_acct,
        "hdfc_cc_last4": hdfc_cc4,
    }


def _derive_password_from_template(session: Session, user_id: str, parser_key: str) -> str:
    from api.models import PasswordTemplate, UserSecrets

    tmpl = session.exec(select(PasswordTemplate).where(PasswordTemplate.parser_key == parser_key)).first()
    if tmpl is None:
        return ""
    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
    if row is None or not row.secrets_json:
        return ""
    try:
        raw_secrets = json.loads(row.secrets_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(raw_secrets, dict):
        return ""
    try:
        required: list[str] = json.loads(tmpl.required_fields_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(required, list) or not required:
        return ""
    kwargs = _logical_kwargs_from_user_secrets(raw_secrets)
    fmt_parts: dict[str, str] = {}
    for name in required:
        key = str(name).strip()
        if key not in kwargs or not str(kwargs[key]).strip():
            return ""
        fmt_parts[key] = str(kwargs[key]).strip()
    try:
        return str(tmpl.password_formula).format(**fmt_parts)
    except (KeyError, ValueError) as e:
        logger.debug("Password template format failed for %s: %s", parser_key, e)
        return ""


def resolve_pdf_password_chain(
    *env_keys: str,
    parser_key: str | None = None,
) -> str:
    """Return the first non-empty secret for ``env_keys`` (UserSecrets + ``.env``).

    If still empty and ``parser_key`` is set (and :func:`~scraper.secrets_context.statement_secrets_context`
    is active), derive from :class:`~api.models.PasswordTemplate` + ingredient keys.
    """
    for key in env_keys:
        v = resolve_secret_env(key, "").strip()
        if v:
            return v
    if not parser_key:
        return ""
    session, uid = get_statement_secrets_scope()
    if session is None or not (uid or "").strip():
        return ""
    return _derive_password_from_template(session, (uid or "").strip(), parser_key)


def is_statement_password_failure(exc: BaseException) -> bool:
    """True for wrong/missing PDF passwords during onboarding (pause + retry)."""
    if isinstance(exc, StatementPasswordRequired):
        return True
    if isinstance(exc, pikepdf.PasswordError):
        return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None and isinstance(cause, BaseException):
        return is_statement_password_failure(cause)
    return False
