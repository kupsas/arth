from pipeline.parsers.base import BaseParser
from pipeline.parsers.hdfc_cc import HDFCCreditCardParser
from pipeline.parsers.hdfc_savings import HDFCSavingsParser
from pipeline.parsers.icici_savings import ICICISavingsParser

PARSER_REGISTRY: dict[str, type[BaseParser]] = {
    "hdfc_savings":   HDFCSavingsParser,
    # Both CC cards share the same parser class — each key points at a different
    # data directory configured in config.py.
    "hdfc_cc_1905":   HDFCCreditCardParser,
    "hdfc_cc_5778":   HDFCCreditCardParser,
    "icici_savings":  ICICISavingsParser,
}
