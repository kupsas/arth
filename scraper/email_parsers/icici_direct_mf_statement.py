"""
ICICI Securities **Mutual Fund Account Statement** email (password-protected PDF).

Sender: ``service@icicisecurities.com``. Same password chain as equity statements.

Emits ``ParsedInvestmentTxn`` rows plus ``ParsedHolding`` rows derived via
:func:`pipeline.holding_parsers.icici_direct_mf.derive_mf_holdings` for the
transactions observed on this statement (snapshot consistent with CSV ingest).
"""

from __future__ import annotations

import datetime
import logging

import pipeline.config  # noqa: F401

from pipeline.holding_parsers.icici_direct_mf import derive_mf_holdings
from pipeline.holding_parsers.icici_direct_mf_statement_pdf import (
    parse_icici_direct_mf_statement_pdf,
)
from pipeline.models import ParsedTransaction
from scraper.email_parsers.base_broker_statement import BaseBrokerStatementParser
from scraper.pdf_passwords import ICICI_DIRECT_STATEMENT_PASSWORD_KEYS, resolve_pdf_password_chain
from scraper.pdf_utils import decrypt_pdf

logger = logging.getLogger(__name__)


def _statement_pdf_password() -> tuple[str, str]:
    p = resolve_pdf_password_chain(*ICICI_DIRECT_STATEMENT_PASSWORD_KEYS)
    return (p, ICICI_DIRECT_STATEMENT_PASSWORD_KEYS[0])


def classify_icici_mf_statement_subject(subject: str) -> bool:
    s = (subject or "").lower()
    return "mutual fund account statement" in s


class ICICIDirectMFStatementEmailParser(BaseBrokerStatementParser):
    """Decrypt MF account statement PDF → MF txns + derived holdings snapshot."""

    def can_parse(self, sender: str, subject: str) -> bool:
        return classify_icici_mf_statement_subject(subject)

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        if not classify_icici_mf_statement_subject(email_subject or ""):
            logger.warning(
                "ICICIDirectMFStatementEmailParser called without MF account statement subject"
            )
            return []

        password, env_key = _statement_pdf_password()
        if not password:
            logger.error(
                "Missing %s — cannot decrypt ICICI MF account statement PDF.",
                env_key,
            )
            return []

        decrypted = decrypt_pdf(pdf_bytes, password)
        try:
            txns = parse_icici_direct_mf_statement_pdf(decrypted)
            self._attachment_inv_txns.extend(txns)
            if txns:
                self._attachment_holdings.extend(derive_mf_holdings(txns))
            else:
                logger.info(
                    "ICICI MF statement PDF produced 0 rows (subject=%r)",
                    (email_subject or "")[:120],
                )
        except Exception:
            logger.exception("ICICI MF account statement PDF parse failed")
            raise
        finally:
            decrypted.unlink(missing_ok=True)

        return []
