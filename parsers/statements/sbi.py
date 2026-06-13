"""
SBI Bank **e-account statement** (Composite Account Statement) emails.

Sender addresses (ICICI-style domain rotation):
  - ``cbssbi.cas@alerts.sbi.bank.in`` (current)
  - ``cbssbi.cas@alerts.sbi.co.in`` (legacy)

Subject: ``E-account statement for your SBI account(s).``

The attachment is a password-protected CAS PDF. Password = last five digits of the
registered mobile number + date of birth as DDMMYY (see ``sbi_statement`` password template).

We decrypt, parse savings transaction tables via
:class:`~parsers.uploads.sbi_savings.SBISavingsParser`, and stamp ``account_id`` /
``source_key`` from the configured last-4 → account map.
"""

from __future__ import annotations

import datetime
import logging

import pikepdf
import pipeline.config  # noqa: F401 — ensures ``.env`` is loaded before ``os.getenv``

from parsers.uploads.sbi_savings import SBISavingsParser
from parsers.statements.base import BaseStatementEmailParser
from pipeline.models import ParsedTransaction
from scraper.pdf_passwords import (
    StatementPasswordRequired,
    resolve_sbi_statement_pdf_password_candidates,
)
from scraper.pdf_utils import decrypt_pdf_with_password_candidates

logger = logging.getLogger(__name__)


def classify_sbi_statement_subject(subject: str) -> bool:
    """True when subject is SBI's monthly e-account (CAS) statement."""
    s = (subject or "").lower()
    # Tolerate ``Fwd:`` prefixes from forwarded mail in tests; production sender is SBI.
    return "e-account statement for your sbi account" in s


class SBIStatementEmailParser(BaseStatementEmailParser):
    """Decrypt SBI CAS PDFs and parse savings cash transactions."""

    def can_parse(self, sender: str, subject: str) -> bool:
        return classify_sbi_statement_subject(subject)

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        del received_date, email_sender, email_subject  # routing is subject-only today

        candidates = resolve_sbi_statement_pdf_password_candidates()
        if not candidates:
            raise StatementPasswordRequired(
                "sbi_statement",
                "Add your SBI registered mobile (last 5 digits) and date of birth in onboarding, "
                "or set SBI_STATEMENT_PASSWORD for local testing.",
            )

        try:
            decrypted, _used_pw = decrypt_pdf_with_password_candidates(pdf_bytes, candidates)
        except pikepdf.PasswordError as e:
            raise StatementPasswordRequired(
                "sbi_statement",
                "None of the SBI PDF password candidates worked. Check mobile last-5 and date of birth.",
            ) from e

        try:
            raw_rows = SBISavingsParser().parse(decrypted)
        except Exception:
            logger.exception("SBI statement PDF parse failed")
            raise
        finally:
            decrypted.unlink(missing_ok=True)

        out: list[ParsedTransaction] = []
        for row in raw_rows:
            last4 = str(row.metadata.get("account_last4") or "").strip()
            if not last4:
                continue
            entry = self.accounts.get(last4)
            if not entry:
                account_id = f"SBI_SAV_{last4}"
                source_key = "sbi_savings"
                logger.info(
                    "SBIStatementEmailParser: no configured slot for savings tail …%s; using %s",
                    last4,
                    account_id,
                )
            else:
                account_id = entry["account_id"]
                source_key = entry["source_key"]
            out.append(_stamp_meta(row, account_id, source_key))
        return out


def _stamp_meta(
    pt: ParsedTransaction, account_id: str, source_key: str
) -> ParsedTransaction:
    return pt.model_copy(
        update={
            "metadata": {
                **pt.metadata,
                "account_id": account_id,
                "source_key": source_key,
                "email_source": "sbi_statement_pdf",
            }
        }
    )
