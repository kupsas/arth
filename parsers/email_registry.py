"""Gmail sender → email parser instances (runtime registry)."""

from __future__ import annotations

from typing import Any

from scraper.config import BANK_SENDERS

from parsers.alerts.base import BaseEmailParser
from parsers.alerts.hdfc import (
    HDFCAccountUpdateParser,
    HDFCCreditCardAlertParser,
    HDFCUPIAlertParser,
)
from parsers.alerts.icici import ICICINetBankingParser
from parsers.statements.hdfc_cc import HDFCCCStatementEmailParser
from parsers.statements.hdfc_combined import HDFCCombinedStatementEmailParser
from parsers.statements.icici import ICICIStatementEmailParser
from parsers.statements.icici_direct_equity import ICICIDirectEquityStatementEmailParser
from parsers.statements.icici_direct_mf import ICICIDirectMFStatementEmailParser
from parsers.statements.icici_direct_trade import ICICIDirectTradeEmailParser


def _hdfc_parser_list(accounts: dict) -> list[BaseEmailParser]:
    """Fresh parser instances per sender address (shared `accounts` dict is OK)."""
    return [
        HDFCCreditCardAlertParser(accounts),
        HDFCUPIAlertParser(accounts),
        HDFCAccountUpdateParser(accounts),
    ]


def build_email_parser_registry(
    bank_senders: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[BaseEmailParser]]:
    """Construct parser instances using account maps from ``bank_senders``.

    When ``bank_senders`` is None, uses static :data:`BANK_SENDERS` (tests / legacy).
    Keys must be normalised sender emails matching the static registry.
    """
    bs = bank_senders if bank_senders is not None else BANK_SENDERS

    def _acct(sender: str) -> dict[str, dict]:
        row = bs.get(sender) or {}
        return row.get("accounts") or {}

    hdfc_a = _acct("alerts@hdfcbank.net")
    icici_stmt = _acct("estatement@icicibank.com")
    hdfc_cc = _acct("emailstatements.cards@hdfcbank.net")
    hdfc_comb = _acct("hdfcbanksmartstatement@hdfcbank.net")
    icici_trade = _acct("nse-direct@nse.co.in")
    icici_sec_svc = _acct("service@icicisecurities.com")

    return {
        "alerts@hdfcbank.net": _hdfc_parser_list(hdfc_a),
        "alerts@hdfcbank.bank.in": _hdfc_parser_list(hdfc_a),
        "customernotification@icici.bank.in": [
            ICICINetBankingParser(_acct("customernotification@icici.bank.in")),
        ],
        "estatement@icicibank.com": [
            ICICIStatementEmailParser(icici_stmt),
        ],
        "estatement@icici.bank.in": [
            ICICIStatementEmailParser(icici_stmt),
        ],
        "customernotification@icicibank.com": [
            ICICIStatementEmailParser(icici_stmt),
        ],
        "emailstatements.cards@hdfcbank.net": [
            HDFCCCStatementEmailParser(hdfc_cc),
        ],
        "emailstatements.cards@hdfcbank.bank.in": [
            HDFCCCStatementEmailParser(hdfc_cc),
        ],
        "hdfcbanksmartstatement@hdfcbank.net": [
            HDFCCombinedStatementEmailParser(hdfc_comb),
        ],
        "hdfcbanksmartstatement@hdfcbank.bank.in": [
            HDFCCombinedStatementEmailParser(hdfc_comb),
        ],
        "ebix@nse.co.in": [
            ICICIDirectTradeEmailParser(icici_trade),
        ],
        "nseinvest@nse.co.in": [
            ICICIDirectTradeEmailParser(icici_trade),
        ],
        "nse-direct@nse.co.in": [
            ICICIDirectTradeEmailParser(icici_trade),
        ],
        "service@icicisecurities.com": [
            ICICIDirectEquityStatementEmailParser(icici_sec_svc),
            ICICIDirectMFStatementEmailParser(icici_sec_svc),
        ],
    }


EMAIL_PARSER_REGISTRY: dict[str, list[BaseEmailParser]] = build_email_parser_registry()

__all__ = ["EMAIL_PARSER_REGISTRY", "build_email_parser_registry"]
