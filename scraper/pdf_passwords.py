"""
Central mapping of bank PDF **logical kinds** → environment variable chains.

Parsers call :func:`resolve_pdf_password_chain` instead of repeating
``os.getenv("A") or os.getenv("B")``. Chains go through
:func:`scraper.secrets_context.resolve_secret_env` so the setup wizard can store
values in ``UserSecrets`` (encrypted) with the same keys as ``.env``.

**Future:** DOB/PAN-derived passwords (DESKTOP_PREREQS item 3) plug in inside
``resolve_secret_env`` or here — without adding seventh ad-hoc env reads in each parser.
"""

from __future__ import annotations

from scraper.secrets_context import resolve_secret_env

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


def resolve_pdf_password_chain(*env_keys: str) -> str:
    """Return the first non-empty secret for ``env_keys`` (UserSecrets + ``.env``)."""
    for key in env_keys:
        v = resolve_secret_env(key, "").strip()
        if v:
            return v
    return ""
