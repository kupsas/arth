"""Uploaded statement files from bank websites."""

from __future__ import annotations

from parsers.uploads.base import BaseParser
from parsers.uploads.hdfc_cc import HDFCCreditCardParser
from parsers.uploads.hdfc_cc_pdf import HDFCCreditCardPdfParser
from parsers.uploads.hdfc_savings import HDFCSavingsParser
from parsers.uploads.hdfc_savings_pdf import HDFCSavingsPdfParser
from parsers.uploads.icici_savings import ICICISavingsParser

PARSER_REGISTRY: dict[str, type[BaseParser]] = {
    "hdfc_savings":   HDFCSavingsParser,
    "hdfc_savings_pdf": HDFCSavingsPdfParser,
    # Both CC cards share the same parser class — each key points at a different
    # data directory configured in config.py.
    "hdfc_cc_1905":   HDFCCreditCardParser,
    "hdfc_cc_5778":   HDFCCreditCardParser,
    "hdfc_cc_pdf":    HDFCCreditCardPdfParser,
    "icici_savings":  ICICISavingsParser,
}

__all__ = [
    "BaseParser",
    "HDFCCreditCardParser",
    "HDFCCreditCardPdfParser",
    "HDFCSavingsParser",
    "HDFCSavingsPdfParser",
    "ICICISavingsParser",
    "PARSER_REGISTRY",
]
