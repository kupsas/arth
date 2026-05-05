"""
ICICI Bank **statement PDF** emails (monthly + annual savings).

These are not InstaAlerts: they carry a password-protected PDF attachment with a
full transaction table. We decrypt, reuse :class:`~pipeline.parsers.icici_savings.ICICISavingsParser`
(same layout as manually uploaded PDFs), then stamp ``account_id`` / ``source_key``
on each row so the orchestrator can group and transform like other email sources.

Routing (sender + subject):

1. **Annual FY** — subject matches ``Bank Statement from DD-MM-YYYY to DD-MM-YYYY``.
   ICICI has sent these from ``estatement@…`` or ``customernotification@icicibank.com``
   (see :data:`~scraper.config.ICICI_SAVINGS_STATEMENT_SENDERS`).
   Savings rows use :class:`~pipeline.parsers.icici_savings.ICICISavingsParser`
   (PPF band stripped); PPF uses :mod:`pipeline.holding_parsers.icici_ppf_pdf`.

2. **Current monthly** — subject contains ``ICICI Bank Statement from`` (post-~Oct 2020).

3. **Legacy monthly** — ``estatement@…`` + ``Bank Statement`` wording without the
   current monthly template (pre-~Oct 2020).

**PDF passwords:** we try every candidate from
:func:`~scraper.pdf_passwords.resolve_icici_statement_pdf_password_candidates`
(env keys, template/PAN, then name+DDMM variants) until one opens the file.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Literal

import pikepdf
import pipeline.config  # noqa: F401 — ensures ``.env`` is loaded before ``os.getenv``

from pipeline.holding_parsers.icici_ppf_pdf import parse_icici_ppf_from_annual_pdf
from pipeline.models import ParsedTransaction
from pipeline.parsers.icici_savings import ICICISavingsParser
from scraper.email_parsers.base_broker_statement import BaseBrokerStatementParser
from scraper.pdf_passwords import (
    StatementPasswordRequired,
    resolve_icici_statement_pdf_password_candidates,
)
from scraper.pdf_utils import decrypt_pdf_with_password_candidates

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

# Same statement product; ICICI has used both From domains.
_LEGACY_MONTHLY_SENDERS: frozenset[str] = frozenset(
    {"estatement@icicibank.com", "estatement@icici.bank.in"}
)


def _is_annual_statement(subject: str) -> bool:
    """FY annual PDF — detect by subject line only (ICICI rotates sender domains)."""
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
    if _is_annual_statement(subject):
        return "annual"
    if _is_current_monthly(subject):
        return "monthly"
    if _is_legacy_monthly(sender, subject):
        return "monthly"
    return None


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

        candidates = resolve_icici_statement_pdf_password_candidates()
        if not candidates:
            raise StatementPasswordRequired(
                "icici_statement",
            "Set ICICI PDF password env vars (see ICICI_STATEMENT_*), or save registered name + "
            "date of birth in onboarding.",
            )

        account_id, source_key = self._icici_savings_account()
        if account_id == "UNKNOWN":
            return []

        try:
            decrypted, _used_pw = decrypt_pdf_with_password_candidates(pdf_bytes, candidates)
        except pikepdf.PasswordError as e:
            raise StatementPasswordRequired(
                "icici_statement",
                "None of the ICICI PDF password candidates worked. Check env keys, date of birth, "
                "and name as on the statement.",
            ) from e
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
