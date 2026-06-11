"""Classify parsed investment ledger rows as mutual fund vs equity."""

from __future__ import annotations

from parsers.holdings.base import ParsedInvestmentTxn
from pipeline.models import AssetClass

_ICICI_MF_PLATFORM = "ICICI Direct MF"


def is_mf_investment_txn(t: ParsedInvestmentTxn) -> bool:
    """True when ``t`` should follow MF holding / dedupe / linking rules."""
    platform = (t.account_platform or "").strip()
    if platform == _ICICI_MF_PLATFORM:
        return True
    meta = t.metadata or {}
    if meta.get("asset_class") == AssetClass.MUTUAL_FUND.value:
        return True
    if meta.get("amfi_scheme_code"):
        return True
    sym = (t.symbol or "").strip()
    if sym.isdigit():
        isin = str(meta.get("isin") or "").strip().upper()
        if isin.startswith("INF"):
            return True
    return False
