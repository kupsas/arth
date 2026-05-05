"""Subject routing for ICICI Securities statement PDF email parsers (WS1 Phase 2)."""

from __future__ import annotations

import pytest

from scraper.email_parsers.icici_direct_equity_statement import (
    ICICIDirectEquityStatementEmailParser,
)
from scraper.email_parsers.icici_direct_mf_statement import (
    ICICIDirectMFStatementEmailParser,
)


@pytest.fixture
def accounts() -> dict:
    return {"0000": {"account_id": "ICICI_DIRECT", "source_key": "icici_direct_equity"}}


@pytest.fixture
def equity_parser(accounts: dict) -> ICICIDirectEquityStatementEmailParser:
    return ICICIDirectEquityStatementEmailParser(accounts)


@pytest.fixture
def mf_parser(accounts: dict) -> ICICIDirectMFStatementEmailParser:
    return ICICIDirectMFStatementEmailParser(accounts)


def test_equity_statement_subject(equity_parser: ICICIDirectEquityStatementEmailParser) -> None:
    assert equity_parser.can_parse(
        "service@icicisecurities.com",
        "Equity Transaction Statement for period 01-Apr-2025 to 31-Mar-2026",
    )


def test_equity_rejects_mf_subject(equity_parser: ICICIDirectEquityStatementEmailParser) -> None:
    assert not equity_parser.can_parse(
        "service@icicisecurities.com",
        "Mutual Fund Account Statement for period 01-Apr-2025 to 31-Mar-2026",
    )


def test_mf_statement_subject(mf_parser: ICICIDirectMFStatementEmailParser) -> None:
    assert mf_parser.can_parse(
        "service@icicisecurities.com",
        "Mutual Fund Account Statement for period 01-Apr-2025 to 31-Mar-2026",
    )


def test_mf_rejects_equity_subject(mf_parser: ICICIDirectMFStatementEmailParser) -> None:
    assert not mf_parser.can_parse(
        "service@icicisecurities.com",
        "Equity Transaction Statement for period 01-Apr-2025 to 31-Mar-2026",
    )


def test_registry_lists_both_parsers_for_service_sender() -> None:
    from scraper.email_parsers import build_email_parser_registry

    reg = build_email_parser_registry()
    parsers = reg["service@icicisecurities.com"]
    assert len(parsers) == 2
    assert isinstance(parsers[0], ICICIDirectEquityStatementEmailParser)
    assert isinstance(parsers[1], ICICIDirectMFStatementEmailParser)
