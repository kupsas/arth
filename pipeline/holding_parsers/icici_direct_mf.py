"""
ICICI Direct mutual fund: RFC-4180 quoted CSV; skip Rejected rows; derive holdings from txns.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pipeline.holding_parsers.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    parse_icici_number,
    strip_bom,
)
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, MutualFundType, ValuationMethod


def _row_get(row: dict[str, str | None], *candidates: str) -> str:
    key_map = {strip_bom((k or "").strip()): v for k, v in row.items()}
    for c in candidates:
        if c in key_map and key_map[c] is not None:
            return str(key_map[c])
    return ""


def parse_icici_mf_csv(path: Path) -> list[ParsedInvestmentTxn]:
    text = strip_bom(path.read_text(encoding="utf-8", errors="replace"))
    reader = csv.DictReader(line.strip() for line in text.splitlines() if line.strip())
    if not reader.fieldnames:
        return []

    out: list[ParsedInvestmentTxn] = []
    for row in reader:
        status = _row_get(row, "Status").strip()
        if status.lower() == "rejected":
            continue

        date_s = _row_get(row, "Date").strip()
        if not date_s:
            continue
        try:
            dt = datetime.strptime(date_s.split(".")[0].strip(), "%d-%b-%Y %H:%M:%S").date()
        except ValueError:
            try:
                dt = datetime.strptime(date_s[:11].strip(), "%d-%b-%Y").date()
            except ValueError:
                continue

        txn_type_raw = _row_get(row, "Transaction Type", "TransactionType").strip()
        channel = _row_get(row, "Channel").strip()
        fund = _row_get(row, "Fund Name", "FundName").strip()
        scheme = _row_get(row, "Scheme Name", "SchemeName").strip()
        folio = _row_get(row, "Folio No", "FolioNo", "Folio").strip()
        # "Last recorded NAV On" is a date column — do not mix into NAV.
        nav = parse_icici_number(_row_get(row, "Last recorded NAV", "Last recorded NAV "))
        amt = parse_icici_number(_row_get(row, "Amount"))
        units = parse_icici_number(_row_get(row, "Unit", "Units"))

        name = f"{fund} — {scheme}".strip(" —") if fund or scheme else scheme or fund or "MF"

        if txn_type_raw.lower() == "purchase":
            if channel.upper() == "SYS":
                txn_type = InvestmentTxnType.SIP.value
            else:
                txn_type = InvestmentTxnType.BUY.value
        elif txn_type_raw.lower() == "redeem":
            txn_type = InvestmentTxnType.SELL.value
        else:
            continue

        qty = abs(units)
        total = abs(amt)
        ppu = abs(nav) if nav else (total / qty if qty else 0.0)
        if not ppu and qty and total:
            ppu = total / qty

        out.append(
            ParsedInvestmentTxn(
                txn_date=dt,
                symbol=None,
                name=name,
                txn_type=txn_type,
                quantity=qty,
                price_per_unit=ppu,
                total_amount=total,
                account_platform="ICICI Direct MF",
                notes=f"Folio {folio}" if folio else None,
                metadata={
                    "source_file": path.name,
                    "folio": folio,
                    "fund_name": fund,
                    "scheme_name": scheme,
                    "channel": channel,
                },
            )
        )
    return out


def derive_mf_holdings(txns: list[ParsedInvestmentTxn]) -> list[ParsedHolding]:
    """Per (scheme, folio): average-cost lot tracking + latest NAV for mark."""
    grouped: dict[tuple[str, str], list[ParsedInvestmentTxn]] = defaultdict(list)
    for t in txns:
        folio = (t.metadata or {}).get("folio") or ""
        key = (t.name or "MF", folio)
        grouped[key].append(t)

    holdings: list[ParsedHolding] = []
    for key, series in grouped.items():
        series.sort(key=lambda x: x.txn_date)
        name, folio = key
        qty_pos = 0.0
        cost_remaining = 0.0
        last_nav = 0.0

        for t in series:
            last_nav = t.price_per_unit or last_nav
            if t.txn_type in (InvestmentTxnType.BUY.value, InvestmentTxnType.SIP.value, InvestmentTxnType.SWITCH_IN.value):
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
        nav = last_nav or avg_remaining or 0.0
        cur_val = nav * qty_pos

        holdings.append(
            ParsedHolding(
                symbol=None,
                name=name,
                quantity=qty_pos,
                asset_class=AssetClass.MUTUAL_FUND.value,
                valuation_method=ValuationMethod.MARKET_PRICE.value,
                account_platform="ICICI Direct MF",
                average_cost_per_unit=avg_remaining,
                current_price_per_unit=nav if nav else None,
                current_value=abs(cur_val),
                liquidity_class=LiquidityClass.T_PLUS_3.value,
                folio_number=folio or None,
                fund_type=MutualFundType.GROWTH.value,
                metadata={"derived_from": "transactions"},
            )
        )
    return holdings


def parse_icici_direct_mf_path(path: Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    txns: list[ParsedInvestmentTxn] = []
    p = path.resolve()
    if p.is_file():
        txns.extend(parse_icici_mf_csv(p))
    elif p.is_dir():
        for f in sorted(p.glob("*.csv")):
            txns.extend(parse_icici_mf_csv(f))
    txns.sort(key=lambda t: t.txn_date)
    holdings = derive_mf_holdings(txns)
    return holdings, txns


class ICICIDirectMFParser(BaseHoldingParser):
    @property
    def source_id(self) -> str:
        return "icici_direct_mf"

    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        return parse_icici_direct_mf_path(Path(path))
