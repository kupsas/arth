"""
Email parser registry.

EMAIL_PARSER_REGISTRY maps each bank sender address to an ordered list of
parser instances. The router tries each parser's can_parse() in order and
uses the first one that returns True.

Order matters: put more specific subject patterns before catch-all ones.
For HDFC, HDFCAccountUpdateParser is last because its subject "Account update"
is the most generic — CC and UPI alerts have more specific subject lines.

Runtime loads per-user account maps via :func:`build_email_parser_registry`
(DESKTOP_PREREQS — config in DB).
"""

from __future__ import annotations

from typing import Any

from scraper.config import BANK_SENDERS
from scraper.email_parsers.base import BaseEmailParser
from scraper.email_parsers.base_statement import BaseStatementEmailParser
from scraper.email_parsers.hdfc_bank import (
    HDFCAccountUpdateParser,
    HDFCCreditCardAlertParser,
    HDFCUPIAlertParser,
)
from scraper.email_parsers.icici_bank import ICICINetBankingParser
from scraper.email_parsers.hdfc_cc_statement import HDFCCCStatementEmailParser
from scraper.email_parsers.hdfc_statement import HDFCCombinedStatementEmailParser
from scraper.email_parsers.icici_direct_trade import ICICIDirectTradeEmailParser
from scraper.email_parsers.icici_statement import ICICIStatementEmailParser


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
    }


EMAIL_PARSER_REGISTRY: dict[str, list[BaseEmailParser]] = build_email_parser_registry()

__all__ = [
    "EMAIL_PARSER_REGISTRY",
    "BaseEmailParser",
    "BaseStatementEmailParser",
    "build_email_parser_registry",
]
