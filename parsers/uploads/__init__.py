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


def register_dynamic_hdfc_savings_key(last4: str) -> str:
    """Ensure ``hdfc_savings_<last4>`` exists in :data:`PARSER_REGISTRY` (same parser class as base).

    Used when a user has more than one HDFC savings account: each gets its own ``source_key``
    so :class:`~api.models.UserPipelineSource` can stay unique per ``(user_id, source_key)``.
    """
    tail = (last4 or "").strip()
    if len(tail) != 4 or not tail.isdigit():
        raise ValueError("last4 must be exactly four digits")
    sk = f"hdfc_savings_{tail}"
    if sk not in PARSER_REGISTRY:
        PARSER_REGISTRY[sk] = HDFCSavingsParser
    return sk


def register_dynamic_hdfc_cc_key(last4: str) -> str:
    """Ensure ``hdfc_cc_<last4>`` maps to :class:`HDFCCreditCardParser` for upload routing."""
    tail = (last4 or "").strip()
    if len(tail) != 4 or not tail.isdigit():
        raise ValueError("last4 must be exactly four digits")
    sk = f"hdfc_cc_{tail}"
    if sk not in PARSER_REGISTRY:
        PARSER_REGISTRY[sk] = HDFCCreditCardParser
    return sk


def register_dynamic_icici_savings_key(last4: str) -> str:
    """Ensure ``icici_savings_<last4>`` maps to :class:`ICICISavingsParser` for a second ICICI account."""
    tail = (last4 or "").strip()
    if len(tail) != 4 or not tail.isdigit():
        raise ValueError("last4 must be exactly four digits")
    sk = f"icici_savings_{tail}"
    if sk not in PARSER_REGISTRY:
        PARSER_REGISTRY[sk] = ICICISavingsParser
    return sk

__all__ = [
    "BaseParser",
    "HDFCCreditCardParser",
    "HDFCCreditCardPdfParser",
    "HDFCSavingsParser",
    "HDFCSavingsPdfParser",
    "ICICISavingsParser",
    "PARSER_REGISTRY",
    "register_dynamic_hdfc_cc_key",
    "register_dynamic_hdfc_savings_key",
    "register_dynamic_icici_savings_key",
]
