"""Routing tests for :class:`scraper.email_parsers.icici_direct_trade.ICICIDirectTradeEmailParser`."""

from __future__ import annotations

import pytest

from scraper.email_parsers.icici_direct_trade import ICICIDirectTradeEmailParser


@pytest.fixture
def parser() -> ICICIDirectTradeEmailParser:
    accounts = {"0000": {"account_id": "ICICI_DIRECT", "source_key": "icici_direct_equity"}}
    return ICICIDirectTradeEmailParser(accounts)


def test_can_parse_nse_executed(parser: ICICIDirectTradeEmailParser) -> None:
    assert parser.can_parse("any@nse.co.in", "Your Trades executed at NSE on 15-03-2024")


def test_rejects_order_confirmation(parser: ICICIDirectTradeEmailParser) -> None:
    assert not parser.can_parse("customercare@icicidirect.com", "Order and Trade confirmations …")


def test_rejects_contract_note_subject(parser: ICICIDirectTradeEmailParser) -> None:
    assert not parser.can_parse("x@y", "NSE Equity Digital Contract Note for your trades")


def test_rejects_savings_statement(parser: ICICIDirectTradeEmailParser) -> None:
    assert not parser.can_parse("estatement@icicibank.com", "Your ICICI Bank Account Statement")
