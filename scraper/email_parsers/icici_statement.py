"""
ICICI Bank **statement PDF** emails (monthly + annual savings).

These are not InstaAlerts: they carry a password-protected PDF attachment with a
full transaction table. We decrypt, reuse :class:`~pipeline.parsers.icici_savings.ICICISavingsParser`
(same layout as manually uploaded PDFs), then stamp ``account_id`` / ``source_key``
on each row so the orchestrator can group and transform like other email sources.

Routing (sender + subject) follows the era table in the email-statement plan:

1. **Annual** — ``customernotification@icicibank.com`` + subject like
   ``Bank Statement from 01-01-2025 to 31-12-2025 for …`` → password
   ``ICICI_STATEMENT_ANNUAL_PASSWORD``. Savings rows use :class:`~pipeline.parsers.icici_savings.ICICISavingsParser`
   (PPF band stripped); PPF uses :mod:`pipeline.holding_parsers.icici_ppf_pdf`.

2. **Current monthly** — subject contains ``ICICI Bank Statement from`` (post-~Oct 2020).
   Some monthly PDFs stack **PPF** then **Savings** on page 1 — same split as annual:
   savings → pipeline transactions; PPF → ``investment_transactions`` + ``holdings``.

3. **Legacy monthly** — ``estatement@icicibank.com`` or ``estatement@icici.bank.in``
   + subject mentions ``Bank Statement`` but not the current monthly wording (pre-~Oct 2020).

Passwords are **not** interchangeable — see ``.env.example`` (monthly vs annual).
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Literal

import pipeline.config  # noqa: F401 — ensures ``.env`` is loaded before ``os.getenv``

from pipeline.holding_parsers.icici_ppf_pdf import parse_icici_ppf_from_annual_pdf
from pipeline.models import ParsedTransaction
from pipeline.parsers.icici_savings import ICICISavingsParser
from scraper.email_parsers.base_broker_statement import BaseBrokerStatementParser
from scraper.pdf_passwords import (
    ICICI_ANNUAL_STATEMENT_PASSWORD_KEYS,
    ICICI_MONTHLY_STATEMENT_PASSWORD_KEYS,
    StatementPasswordRequired,
    resolve_pdf_password_chain,
)
from scraper.pdf_utils import decrypt_pdf

logger = logging.getLogger(__name__)

# Same logic as ``email_router._normalise_sender`` — duplicated here to avoid a
# circular import (``email_router`` imports ``EMAIL_PARSER_REGISTRY`` from this package).
_SENDER_IN_BRACKETS = re.compile(r"<([^>]+)>")


def _normalise_sender(raw: str) -> str:
    m = _SENDER_IN_BRACKETS.search(raw)
    return (m.group(1).strip().lower() if m else raw.strip().lower())

# Annual FY line in subject (distinct from legacy monthly wording on estatement).
_ANNUAL_SUBJECT_RE = re.compile(
    r"bank\s+statement\s+from\s+\d{2}-\d{2}-\d{4}\s+to\s+\d{2}-\d{2}-\d{4}",
    re.IGNORECASE,
)

_SENDER_ANNUAL = "customernotification@icicibank.com"
# Same statement product; ICICI has used both From domains.
_LEGACY_MONTHLY_SENDERS: frozenset[str] = frozenset(
    {"estatement@icicibank.com", "estatement@icici.bank.in"}
)


def _is_annual_statement(sender: str, subject: str) -> bool:
    if sender != _SENDER_ANNUAL:
        return False
    if "bank statement from" not in subject.lower():
        return False
    return bool(_ANNUAL_SUBJECT_RE.search(subject))


def _is_current_monthly(subject: str) -> bool:
    return "icici bank statement from" in subject.lower()


def _is_legacy_monthly(sender: str, subject: str) -> bool:
    """Pre-2020-ish monthly: estatement + 'Bank Statement' wording, not annual line."""
    if sender not in _LEGACY_MONTHLY_SENDERS:
        return False
    if _is_current_monthly(subject):
        return False
    return "bank statement" in subject.lower()


def _statement_kind(
    sender: str, subject: str
) -> Literal["annual", "monthly"] | None:
    """Classify which password and expectations apply — must stay in sync with :meth:`can_parse`."""
    if _is_annual_statement(sender, subject):
        return "annual"
    if _is_current_monthly(subject):
        return "monthly"
    if _is_legacy_monthly(sender, subject):
        return "monthly"
    return None


def _monthly_password() -> str:
    return resolve_pdf_password_chain(
        *ICICI_MONTHLY_STATEMENT_PASSWORD_KEYS,
        parser_key="icici_statement_monthly",
    )


def _annual_password() -> str:
    return resolve_pdf_password_chain(
        *ICICI_ANNUAL_STATEMENT_PASSWORD_KEYS,
        parser_key="icici_statement_annual",
    )


class ICICIStatementEmailParser(BaseBrokerStatementParser):
    """Decrypt ICICI statement PDFs and parse savings transactions."""

    def can_parse(self, sender: str, subject: str) -> bool:
        return _statement_kind(sender, subject) is not None

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        sender = _normalise_sender(email_sender or "")
        subject = email_subject or ""
        kind = _statement_kind(sender, subject)
        if kind is None:
            logger.warning(
                "ICICIStatementEmailParser.parse_attachment called but "
                "subject/sender would not match can_parse (sender=%r subject=%r)",
                sender,
                subject[:120],
            )
            return []

        password = _annual_password() if kind == "annual" else _monthly_password()
        if not password:
            pkey = "icici_statement_annual" if kind == "annual" else "icici_statement_monthly"
            raise StatementPasswordRequired(
                pkey,
                "Set ICICI PDF password env vars, save ingredients in onboarding, or add UserSecrets keys.",
            )

        account_id, source_key = self._icici_savings_account()
        if account_id == "UNKNOWN":
            return []

        decrypted = decrypt_pdf(pdf_bytes, password)
        try:
            raw_rows = ICICISavingsParser().parse(decrypted)
            # Combined PDFs (annual or monthly) may include a PPF table above savings.
            # PPF rows must not be stamped as savings — extract them here in tandem.
            ph, pt = parse_icici_ppf_from_annual_pdf(
                decrypted,
                reference_date=received_date,
                source_label=(
                    "icici_annual_statement_email"
                    if kind == "annual"
                    else "icici_monthly_statement_email"
                ),
            )
            self._attachment_holdings.extend(ph)
            self._attachment_inv_txns.extend(pt)
        except Exception:
            logger.exception(
                "ICICI statement PDF parse failed (kind=%s)", kind
            )
            raise
        finally:
            decrypted.unlink(missing_ok=True)

        return [_stamp_meta(r, account_id, source_key) for r in raw_rows]

    def _icici_savings_account(self) -> tuple[str, str]:
        """Map config to the single ICICI savings account used for statement PDFs."""
        if "6118" in self.accounts:
            e = self.accounts["6118"]
            return e["account_id"], e["source_key"]
        if len(self.accounts) == 1:
            e = next(iter(self.accounts.values()))
            return e["account_id"], e["source_key"]
        logger.warning(
            "ICICIStatementEmailParser: expected account 6118 in accounts, found %s",
            list(self.accounts.keys()),
        )
        return "UNKNOWN", "unknown"


def _stamp_meta(
    pt: ParsedTransaction, account_id: str, source_key: str
) -> ParsedTransaction:
    """Parsed rows from the PDF parser have no account metadata — add it (model is frozen)."""
    return pt.model_copy(
        update={
            "metadata": {
                **pt.metadata,
                "account_id": account_id,
                "source_key": source_key,
                "email_source": "icici_statement_pdf",
            }
        }
    )
