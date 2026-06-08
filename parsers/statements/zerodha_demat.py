"""
Zerodha **Monthly Demat Transaction** email (password-protected PDF).

Sender (production): ``no-reply-transaction-with-holding-statement@reportsmailer.zerodha.net``.
Subject: ``Zerodha Broking Ltd: Monthly Demat Transaction`` (forwarded subjects with ``Fwd:`` OK).

Password: PAN from onboarding ``UserSecrets`` (see :func:`~scraper.pdf_passwords.resolve_zerodha_demat_pdf_password_candidates`).
"""

from __future__ import annotations

import datetime
import logging

import pikepdf
import pipeline.config  # noqa: F401 — load ``.env``

from parsers.holdings.zerodha_demat_statement_pdf import parse_zerodha_demat_statement_pdf
from pipeline.models import ParsedTransaction
from parsers.statements.base_broker import BaseBrokerStatementParser
from scraper.pdf_passwords import (
    StatementPasswordRequired,
    resolve_zerodha_demat_pdf_password_candidates,
)
from scraper.pdf_utils import decrypt_pdf_with_password_candidates

logger = logging.getLogger(__name__)


def classify_zerodha_demat_statement_subject(subject: str) -> bool:
    """True when subject is Zerodha's monthly demat transaction statement."""
    s = (subject or "").lower()
    return "monthly demat transaction" in s


class ZerodhaDematStatementEmailParser(BaseBrokerStatementParser):
    """Decrypt Zerodha demat PDF → Statement-of-Account legs (not holdings snapshot)."""

    def can_parse(self, sender: str, subject: str) -> bool:
        return classify_zerodha_demat_statement_subject(subject)

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        if not classify_zerodha_demat_statement_subject(email_subject or ""):
            logger.warning(
                "ZerodhaDematStatementEmailParser called without demat statement subject"
            )
            return []

        candidates = resolve_zerodha_demat_pdf_password_candidates()
        if not candidates:
            raise StatementPasswordRequired(
                "zerodha_demat_statement",
                "Save your PAN in onboarding PDF ingredients for Zerodha demat statements.",
            )

        try:
            decrypted, _used = decrypt_pdf_with_password_candidates(pdf_bytes, candidates)
        except pikepdf.PasswordError as e:
            raise StatementPasswordRequired(
                "zerodha_demat_statement",
                "None of the Zerodha demat PDF password candidates worked.",
            ) from e

        try:
            _holdings_unused, rows = parse_zerodha_demat_statement_pdf(decrypted, aggregate=True)
            self._attachment_inv_txns.extend(rows)
            if rows:
                from parsers.holdings.derived_equity import derive_equity_holdings

                self._attachment_holdings.extend(derive_equity_holdings(rows, platform="Zerodha"))
            if not rows:
                logger.info(
                    "Zerodha demat statement PDF produced 0 rows (subject_len=%d)",
                    len(email_subject or ""),
                )
        except Exception:
            logger.exception("Zerodha demat statement PDF parse failed")
            raise
        finally:
            decrypted.unlink(missing_ok=True)

        return []
