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
import re
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
# ICICI Direct / ICICI Sec equity + MF **statement** PDFs from email (Phase WS1).
ICICI_DIRECT_STATEMENT_PASSWORD_KEYS = (
    "ICICI_STATEMENT_MONTHLY_PASSWORD",
    "ICICI_DIRECT_EMAIL_PASSWORD",
)
# Zerodha monthly demat statement — test-only env override; production uses PAN from UserSecrets.
ZERODHA_DEMAT_STATEMENT_PASSWORD_KEYS = ("ZERODHA_DEMAT_STATEMENT_PASSWORD",)

# Env keys tried **in order** for ICICI-issued statement PDFs (savings e-statements +
# ICICI Securities equity/MF PDFs). ICICI has rotated sender addresses and password *formats*
# (legacy lowercase full-name + DDMM vs newer first-four-uppercase + DDMM); trying every
# configured secret covers monthly vs annual overrides and broker mail without mapping each
# sender to exactly one password.
ICICI_STATEMENT_PDF_ENV_KEYS: tuple[str, ...] = (
    "ICICI_STATEMENT_PASSWORD",
    "ICICI_STATEMENT_MONTHLY_PASSWORD",
    "ICICI_STATEMENT_ANNUAL_PASSWORD",
    "ICICI_DIRECT_EMAIL_PASSWORD",
    "ICICI_DIRECT_TRADE_PASSWORD",
)

# Stored in UserSecrets.secrets_json — onboarding form merges these alongside env-key mirrors.
ARTH_PDF_INGREDIENT_PAN = "ARTH_PDF_INGREDIENT_PAN"
ARTH_PDF_INGREDIENT_DOB_ISO = "ARTH_PDF_INGREDIENT_DOB_ISO"
ARTH_PDF_INGREDIENT_ICICI_REGISTERED_NAME = "ARTH_PDF_INGREDIENT_ICICI_REGISTERED_NAME"
ARTH_PDF_INGREDIENT_HDFC_ACCOUNT_NUMBER = "ARTH_PDF_INGREDIENT_HDFC_ACCOUNT_NUMBER"
ARTH_PDF_INGREDIENT_HDFC_CC_LAST4 = "ARTH_PDF_INGREDIENT_HDFC_CC_LAST4"
ARTH_PDF_INGREDIENT_HDFC_CUSTOMER_ID = "ARTH_PDF_INGREDIENT_HDFC_CUSTOMER_ID"

# ``scraper.config.BANK_SENDERS`` ``parser_key`` → :class:`~api.models.PasswordTemplate.parser_key` rows.
EMAIL_PARSER_KEY_TO_PASSWORD_TEMPLATE_KEYS: dict[str, tuple[str, ...]] = {
    "icici_statement": ("icici_statement_monthly", "icici_statement_annual"),
    "hdfc_combined_statement": ("hdfc_combined_statement",),
    "hdfc_cc_statement": ("hdfc_cc_statement",),
    # ICICI Securities equity/MF statement PDFs — same name+DDMM family as ICICI Bank PDFs.
    "icici_direct_statement": ("icici_statement_monthly",),
    "zerodha_demat_statement": ("zerodha_demat_statement",),
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


def _dob_iso_to_ddmm(iso: str) -> str:
    """Turn ``YYYY-MM-DD`` into ``DDMM`` (day + month only — ICICI PDF pattern)."""
    raw = (iso or "").strip()[:10]
    parts = raw.split("-")
    if len(parts) != 3:
        return ""
    try:
        _y, m, d = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return ""
    if m < 1 or m > 12 or d < 1 or d > 31:
        return ""
    return f"{d:02d}{m:02d}"


def _first4_alpha_only(name: str) -> str:
    """Strip non-letters, take first four — ICICI/HDFC CC PDF passwords use this base."""
    letters_only = re.sub(r"[^a-zA-Z]", "", (name or "").strip())
    return letters_only[:4]


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


def _json_alias_list(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return []


def _pdf_identity_name_strings(
    session: Session | None,
    user_id: str | None,
    raw_secrets: dict[str, Any],
) -> list[str]:
    """Ordered unique name strings for PDF passwords — explicit secret, then profile.

    Primary source is :class:`~api.models.UserClassificationSettings` (``self_name`` +
    ``self_aliases_json`` from preclassification). Optional override:
    ``ARTH_PDF_INGREDIENT_ICICI_REGISTERED_NAME`` in ``UserSecrets`` (legacy / power users).
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        t = (s or "").strip()
        if not t:
            return
        k = t.casefold()
        if k in seen:
            return
        seen.add(k)
        ordered.append(t)

    expl = str(raw_secrets.get(ARTH_PDF_INGREDIENT_ICICI_REGISTERED_NAME, "")).strip()
    if expl:
        add(expl)

    if session is None or not (user_id or "").strip():
        return ordered

    from api.models import UserClassificationSettings

    row = session.exec(
        select(UserClassificationSettings).where(UserClassificationSettings.user_id == user_id)
    ).first()
    if row is None:
        return ordered
    if (row.self_name or "").strip():
        add(row.self_name.strip())
    for a in _json_alias_list(row.self_aliases_json):
        add(a)
    return ordered


def build_pdf_template_kwargs(
    session: Session | None,
    user_id: str | None,
    raw_secrets: dict[str, Any],
) -> dict[str, str]:
    """Map PasswordTemplate placeholders → values (identity names come from profile when unset)."""
    pan = str(raw_secrets.get(ARTH_PDF_INGREDIENT_PAN, "")).strip().upper()
    dob_iso_raw = str(raw_secrets.get(ARTH_PDF_INGREDIENT_DOB_ISO, "")).strip()
    dob_iso = dob_iso_raw[:10]
    hdfc_acct = str(raw_secrets.get(ARTH_PDF_INGREDIENT_HDFC_ACCOUNT_NUMBER, "")).strip()
    hdfc_cc4 = str(raw_secrets.get(ARTH_PDF_INGREDIENT_HDFC_CC_LAST4, "")).strip()
    hdfc_cust = str(raw_secrets.get(ARTH_PDF_INGREDIENT_HDFC_CUSTOMER_ID, "")).strip()

    names = _pdf_identity_name_strings(session, user_id, raw_secrets)
    icici_reg = names[0] if names else str(raw_secrets.get(ARTH_PDF_INGREDIENT_ICICI_REGISTERED_NAME, "")).strip()
    first4 = _first4_alpha_only(icici_reg)
    return {
        "pan": pan,
        "dob_iso": dob_iso,
        "dob_ddmmyyyy": _dob_iso_to_ddmmyyyy(dob_iso),
        "dob_ddmm": _dob_iso_to_ddmm(dob_iso),
        "hdfc_account_number": hdfc_acct,
        "hdfc_customer_id": hdfc_cust,
        "hdfc_cc_last4": hdfc_cc4,
        "icici_registered_name": icici_reg,
        "icici_first4_upper": first4.upper(),
        "icici_first4_lower": first4.lower(),
        "icici_name_lower_nospace": re.sub(r"\s+", "", icici_reg).lower(),
        "icici_name4_upper": first4.upper(),
    }


def list_pdf_password_holder_names(session: Session, user_id: str) -> list[str]:
    """Public: every name string used to derive FIRST4+DDMM PDF passwords (override + profile)."""
    raw: dict[str, Any] = {}
    from api.models import UserSecrets

    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
    if row is not None and row.secrets_json:
        try:
            j = json.loads(row.secrets_json)
            if isinstance(j, dict):
                raw = j
        except json.JSONDecodeError:
            pass
    return _pdf_identity_name_strings(session, user_id, raw)


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
    kwargs = build_pdf_template_kwargs(session, user_id, raw_secrets)
    for name in required:
        key = str(name).strip()
        if key not in kwargs or not str(kwargs[key]).strip():
            return ""
    formula = str(tmpl.password_formula or "")
    if ("{icici_first4_upper}" in formula or "{icici_first4_lower}" in formula) and not (
        kwargs.get("icici_first4_upper") or ""
    ).strip():
        return ""
    try:
        return str(tmpl.password_formula).format(**kwargs)
    except (KeyError, ValueError) as e:
        logger.debug("Password template format failed for %s: %s", parser_key, e)
        return ""


def _derive_name_dob_password_variants_for_holder_names(name_strings: list[str], dob_iso: str) -> list[str]:
    """FIRST4+DDMM for each holder-name variant (dedupe)."""
    ddmm = _dob_iso_to_ddmm(dob_iso)
    if len(ddmm) != 4:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for name_raw in name_strings:
        first4 = _first4_alpha_only(name_raw)
        if len(first4) < 1:
            continue
        for cand in (f"{first4.upper()}{ddmm}", f"{first4.lower()}{ddmm}"):
            if cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def _derive_name_dob_variants_from_secrets(
    session: Session | None,
    user_id: str | None,
    raw_secrets: dict[str, Any],
) -> list[str]:
    dob_iso = str(raw_secrets.get(ARTH_PDF_INGREDIENT_DOB_ISO, "")).strip()
    if not dob_iso:
        return []
    names = _pdf_identity_name_strings(session, user_id, raw_secrets)
    if not names:
        return []
    return _derive_name_dob_password_variants_for_holder_names(names, dob_iso)


def resolve_icici_statement_pdf_password_candidates() -> list[str]:
    """Every candidate password to try for ICICI savings + ICICI Direct statement PDFs.

    Order: explicit env/UserSecrets keys (see :data:`ICICI_STATEMENT_PDF_ENV_KEYS`), then
    template-derived strings (when DB recipes exist), then name+DDMM permutations from
    onboarding ingredients.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        s = (p or "").strip()
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)

    for key in ICICI_STATEMENT_PDF_ENV_KEYS:
        _add(resolve_secret_env(key, ""))

    session, uid = get_statement_secrets_scope()
    user_id = (uid or "").strip()
    if session is not None and user_id:
        for pk in ("icici_statement_monthly", "icici_statement_annual"):
            _add(_derive_password_from_template(session, user_id, pk))
        from api.models import UserSecrets

        row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
        if row is not None and row.secrets_json:
            try:
                raw_secrets = json.loads(row.secrets_json)
            except json.JSONDecodeError:
                raw_secrets = {}
            if isinstance(raw_secrets, dict):
                for v in _derive_name_dob_variants_from_secrets(session, user_id, raw_secrets):
                    _add(v)

    return ordered


def resolve_zerodha_demat_pdf_password_candidates() -> list[str]:
    """Zerodha monthly demat statement PDF: optional test env, then PAN from UserSecrets template."""
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        s = (p or "").strip().upper()
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)

    for key in ZERODHA_DEMAT_STATEMENT_PASSWORD_KEYS:
        _add(resolve_secret_env(key, ""))

    session, uid = get_statement_secrets_scope()
    user_id = (uid or "").strip()
    if session is not None and user_id:
        _add(_derive_password_from_template(session, user_id, "zerodha_demat_statement"))

    return ordered


def resolve_hdfc_combined_pdf_password_candidates() -> list[str]:
    """HDFC combined savings PDF: ``HDFC_STATEMENT_PASSWORD`` env chain, then customer ID ingredient."""
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        s = (p or "").strip()
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)

    for key in HDFC_COMBINED_STATEMENT_PASSWORD_KEYS:
        _add(resolve_secret_env(key, ""))

    session, uid = get_statement_secrets_scope()
    user_id = (uid or "").strip()
    if session is not None and user_id:
        _add(_derive_password_from_template(session, user_id, "hdfc_combined_statement"))
    return ordered


def resolve_hdfc_cc_pdf_password_candidates() -> list[str]:
    """HDFC credit card PDF: env chain, then recipe / name+DDMM (same first-four algorithm as ICICI)."""
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        s = (p or "").strip()
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)

    for key in HDFC_CC_STATEMENT_PASSWORD_KEYS:
        _add(resolve_secret_env(key, ""))

    session, uid = get_statement_secrets_scope()
    user_id = (uid or "").strip()
    if session is not None and user_id:
        _add(_derive_password_from_template(session, user_id, "hdfc_cc_statement"))
        from api.models import UserSecrets

        row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
        if row is not None and row.secrets_json:
            try:
                raw_secrets = json.loads(row.secrets_json)
            except json.JSONDecodeError:
                raw_secrets = {}
            if isinstance(raw_secrets, dict):
                for v in _derive_name_dob_variants_from_secrets(session, user_id, raw_secrets):
                    _add(v)

    return ordered


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
