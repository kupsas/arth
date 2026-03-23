"""
Portfolio / investment parsers (Phase A.2).

Each parser implements ``BaseHoldingParser`` and is registered in
``HOLDING_PARSER_REGISTRY`` for CLI ingest.
"""

from __future__ import annotations

from pipeline.holding_parsers.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    ParsedLiability,
)
from pipeline.holding_parsers.icici_direct_equity import ICICIDirectEquityParser
from pipeline.holding_parsers.icici_direct_mf import ICICIDirectMFParser
from pipeline.holding_parsers.icici_ppf import ICICIPPFParser
from pipeline.holding_parsers.liabilities import parse_bike_loan_txt, parse_term_insurance_pdf
from pipeline.holding_parsers.nps import NPSParser

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
