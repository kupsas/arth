"""
ICICI Direct **equity** rows from the **Trades executed at NSE** email only (PDF → DB).

The exchange email (e.g. ``nse-direct@nse.co.in``) sends a password-protected PDF listing
NSE symbol, buy/sell, quantity, and rate. Password: ``NSE_TRADES_EXECUTED_PASSWORD``.

Parsing: :mod:`pipeline.holding_parsers.icici_direct_contract_note`. No bank ``transform``/LLM —
only :func:`pipeline.holding_pipeline.ingest_investment_transactions` with ``source_type=email``.

ICICI *Order and Trade* / contract-note PDFs are **not** ingested here (by design).
"""

from __future__ import annotations

import datetime
import logging

import pipeline.config  # noqa: F401 — load ``.env`` before ``os.getenv``

from pipeline.holding_parsers.icici_direct_contract_note import parse_icici_direct_trade_pdf
from pipeline.models import ParsedTransaction
from scraper.email_parsers.base_broker_statement import BaseBrokerStatementParser
from scraper.pdf_passwords import (
    NSE_TRADES_EXECUTED_PASSWORD_KEYS,
    StatementPasswordRequired,
    resolve_pdf_password_chain,
)
from scraper.pdf_utils import decrypt_pdf

logger = logging.getLogger(__name__)

# Subject must contain this phrase (case-insensitive), same as live NSE mailers use.
_NSE_TRADES_SUBJECT_PHRASE = "trades executed at nse"


def classify_icici_direct_subject(subject: str) -> str | None:
    """Return a token if *subject* is the NSE trades email; else ``None``."""
    s = (subject or "").lower()
    if _NSE_TRADES_SUBJECT_PHRASE in s:
        return "nse_trades_executed"
    return None


def _nse_trades_pdf_password() -> tuple[str, str]:
    """Password for NSE-originated trade PDFs and primary env key for error messages."""
    p = resolve_pdf_password_chain(
        *NSE_TRADES_EXECUTED_PASSWORD_KEYS,
        parser_key="nse_trades_executed",
    )
    return (p, NSE_TRADES_EXECUTED_PASSWORD_KEYS[0])


class ICICIDirectTradeEmailParser(BaseBrokerStatementParser):
    """Decrypt the NSE trades PDF and emit investment rows (no bank ledger rows)."""

    def can_parse(self, sender: str, subject: str) -> bool:
        return classify_icici_direct_subject(subject) is not None

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        """Return ``[]`` — investment rows go via :meth:`attachment_investment_outputs`."""
        if classify_icici_direct_subject(email_subject or "") is None:
            logger.warning(
                "ICICIDirectTradeEmailParser.parse_attachment called without "
                "a *Trades executed at NSE* subject"
            )
            return []

        password, env_name = _nse_trades_pdf_password()
        if not password:
            raise StatementPasswordRequired(
                "nse_trades_executed",
                f"Set {env_name}, ICICI_DIRECT_TRADE_PASSWORD, or PAN ingredient for derived passwords.",
            )

        decrypted = decrypt_pdf(pdf_bytes, password)
        try:
            self._attachment_inv_txns.extend(
                parse_icici_direct_trade_pdf(
                    decrypted,
                    fallback_trade_date=received_date,
                    aggregate=True,
                )
            )
            if not self._attachment_inv_txns:
                logger.info(
                    "NSE trades PDF produced 0 rows (subject=%r) — layout may need parser tweaks",
                    (email_subject or "")[:100],
                )
        except Exception:
            logger.exception("NSE trades PDF parse failed")
            raise
        finally:
            decrypted.unlink(missing_ok=True)

        return []
