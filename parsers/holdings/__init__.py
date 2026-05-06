"""
Portfolio / investment parsers (Phase A.2).

Each parser implements ``BaseHoldingParser`` and is registered in
``HOLDING_PARSER_REGISTRY`` for CLI ingest.
"""

from __future__ import annotations

from parsers.holdings.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    ParsedLiability,
)
from parsers.holdings.icici_direct_equity import ICICIDirectEquityParser
from parsers.holdings.icici_direct_mf import ICICIDirectMFParser
from parsers.holdings.icici_ppf import ICICIPPFParser
from parsers.holdings.liabilities import parse_bike_loan_txt, parse_term_insurance_pdf
from parsers.holdings.nps import NPSParser

HOLDING_PARSER_REGISTRY: dict[str, type[BaseHoldingParser]] = {
    "icici_direct_equity": ICICIDirectEquityParser,
    "icici_direct_mf": ICICIDirectMFParser,
    "icici_ppf": ICICIPPFParser,
    "nps": NPSParser,
}

__all__ = [
    "BaseHoldingParser",
    "HOLDING_PARSER_REGISTRY",
    "ParsedHolding",
    "ParsedInvestmentTxn",
    "ParsedLiability",
    "parse_bike_loan_txt",
    "parse_term_insurance_pdf",
]
