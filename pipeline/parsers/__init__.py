"""Backward-compatible shim: upload parsers live under ``parsers.uploads``."""

from parsers.uploads import (
    PARSER_REGISTRY,
    BaseParser,
    HDFCCreditCardParser,
    HDFCCreditCardPdfParser,
    HDFCSavingsParser,
    HDFCSavingsPdfParser,
    ICICISavingsParser,
)

__all__ = [
    "PARSER_REGISTRY",
    "BaseParser",
    "HDFCCreditCardParser",
    "HDFCCreditCardPdfParser",
    "HDFCSavingsParser",
    "HDFCSavingsPdfParser",
    "ICICISavingsParser",
]
