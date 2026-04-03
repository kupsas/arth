"""Routing tests for :class:`HDFCCombinedStatementEmailParser`."""

from __future__ import annotations

import pytest

from scraper.config import BANK_SENDERS
from scraper.email_parsers.hdfc_statement import HDFCCombinedStatementEmailParser


@pytest.fixture
def parser() -> HDFCCombinedStatementEmailParser:
    accounts = BANK_SENDERS["hdfcbanksmartstatement@hdfcbank.net"]["accounts"]
    return HDFCCombinedStatementEmailParser(accounts)


def test_can_parse_combined_monthly(parser: HDFCCombinedStatementEmailParser) -> None:
    assert parser.can_parse(
        "hdfcbanksmartstatement@hdfcbank.net",
        "HDFC Bank Combined Email Statement for March-2026",
    )


def test_rejects_legacy_email_account_statement(parser: HDFCCombinedStatementEmailParser) -> None:
    """Pre-2024 product name — different PDF layout; not handled in Phase 3."""
    assert not parser.can_parse(
        "hdfcbanksmartstatement@hdfcbank.net",
        "Email Account Statement of your HDFC Bank Account 3703 for the period ...",
    )


def test_rejects_instalert(parser: HDFCCombinedStatementEmailParser) -> None:
    assert not parser.can_parse(
        "alerts@hdfcbank.net",
        "UPI transaction on your HDFC Bank account",
    )
