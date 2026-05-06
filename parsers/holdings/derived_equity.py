"""
Derive :class:`ParsedHolding` snapshots from ICICI Direct **equity** ledger rows.

Mirrors :func:`parsers.holdings.icici_direct_mf.derive_mf_holdings` but groups by
NSE ``symbol`` (no folio). Only rows with ``account_platform == \"ICICI Direct\"`` are
included — **not** ``ICICI Direct MF``.
"""

from __future__ import annotations

from collections import defaultdict

from parsers.holdings.base import ParsedHolding, ParsedInvestmentTxn
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod

# Equity cash-market ledger from ICICI Securities (CSV, contract notes, transaction statements).
_ICICI_DIRECT_EQUITY_PLATFORM = "ICICI Direct"


def derive_equity_holdings(txns: list[ParsedInvestmentTxn]) -> list[ParsedHolding]:
    """Per NSE symbol: average-cost lot tracking; last trade price as mark.

    Ignores mutual-fund platforms and rows without a usable ``symbol``.
    """
    grouped: dict[str, list[ParsedInvestmentTxn]] = defaultdict(list)
    for t in txns:
        if (t.account_platform or "").strip() != _ICICI_DIRECT_EQUITY_PLATFORM:
            continue
        sym = (t.symbol or "").strip()
        if not sym:
            continue
        grouped[sym].append(t)

    holdings: list[ParsedHolding] = []
    for symbol, series in grouped.items():
        series.sort(key=lambda x: x.txn_date)
        qty_pos = 0.0
        cost_remaining = 0.0
        last_px = 0.0
        display_name = symbol

        for t in series:
            last_px = t.price_per_unit or last_px
            if t.name and str(t.name).strip():
                display_name = str(t.name).strip()
            if t.txn_type in (
                InvestmentTxnType.BUY.value,
                InvestmentTxnType.SIP.value,
                InvestmentTxnType.SWITCH_IN.value,
            ):
                qty_pos += t.quantity
                cost_remaining += t.total_amount
            elif t.txn_type in (InvestmentTxnType.SELL.value, InvestmentTxnType.SWITCH_OUT.value):
                if qty_pos <= 0:
                    continue
                avg_cost = cost_remaining / qty_pos
                red = min(t.quantity, qty_pos)
                cost_remaining -= avg_cost * red
                qty_pos -= red

        if qty_pos < 1e-9:
            continue
        avg_remaining = cost_remaining / qty_pos if qty_pos else None
        mark = last_px or avg_remaining or 0.0
        cur_val = mark * qty_pos

        holdings.append(
            ParsedHolding(
                symbol=symbol,
                name=display_name,
                quantity=qty_pos,
                asset_class=AssetClass.EQUITY.value,
                valuation_method=ValuationMethod.MARKET_PRICE.value,
                account_platform=_ICICI_DIRECT_EQUITY_PLATFORM,
                average_cost_per_unit=avg_remaining,
                current_price_per_unit=mark if mark else None,
                current_value=abs(cur_val),
                liquidity_class=LiquidityClass.T_PLUS_1.value,
                metadata={"derived_from": "transactions"},
            )
        )
    return holdings
