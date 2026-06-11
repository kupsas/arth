"""
ICICI Direct mutual fund: RFC-4180 quoted CSV; skip Rejected rows; derive holdings from txns.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pipeline.detection import DetectionResult, PARSER_LABELS
from parsers.holdings.base import (
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
        scheme_code = _row_get(row, "Scheme Code", "SchemeCode", "SCRIP CODE", "Scrip Code").strip()
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

        amfi_meta = scheme_code if scheme_code.isdigit() else None
        out.append(
            ParsedInvestmentTxn(
                txn_date=dt,
                symbol=amfi_meta,
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
                    "amfi_scheme_code": amfi_meta,
                },
            )
        )
    return out


def _mf_group_key(t: ParsedInvestmentTxn) -> tuple[str, str]:
    meta = t.metadata or {}
    folio = str(meta.get("folio") or "").strip()
    code = meta.get("amfi_scheme_code") or (
        t.symbol if (t.symbol or "").strip().isdigit() else None
    )
    if code:
        return (str(code).strip(), folio)
    isin = str(meta.get("isin") or "").strip().upper()
    if isin:
        return (isin, folio)
    return (t.name or "MF", folio)


def derive_mf_holdings(
    txns: list[ParsedInvestmentTxn],
    *,
    platform: str = "ICICI Direct MF",
) -> list[ParsedHolding]:
    """Per (AMFI code or name, folio): average-cost lot tracking + latest NAV for mark."""
    plat = (platform or "").strip()
    grouped: dict[tuple[str, str], list[ParsedInvestmentTxn]] = defaultdict(list)
    for t in txns:
        grouped[_mf_group_key(t)].append(t)

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

        sch_sym: str | None = None
        amfi_meta: str | None = None
        display_name = name
        for t in series:
            raw = (t.metadata or {}).get("amfi_scheme_code") or t.symbol
            if raw and str(raw).strip().isdigit():
                sch_sym = str(raw).strip()
                amfi_meta = sch_sym
                break
            if t.name and str(t.name).strip():
                display_name = str(t.name).strip()

        holdings.append(
            ParsedHolding(
                symbol=sch_sym,
                name=display_name,
                quantity=qty_pos,
                asset_class=AssetClass.MUTUAL_FUND.value,
                valuation_method=ValuationMethod.MARKET_PRICE.value,
                account_platform=plat,
                average_cost_per_unit=avg_remaining,
                current_price_per_unit=nav if nav else None,
                current_value=abs(cur_val),
                liquidity_class=LiquidityClass.T_PLUS_3.value,
                folio_number=folio or None,
                fund_type=MutualFundType.GROWTH.value,
                amfi_scheme_code=amfi_meta,
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

    @classmethod
    def detect(cls, path: str | Path) -> DetectionResult | None:
        """Quoted MF ledger CSV from ICICI Direct."""
        p = Path(path)
        if p.is_dir():
            for f in sorted(p.glob("*.csv")):
                hit = cls.detect(f)
                if hit:
                    return hit
            return None
        if p.suffix.lower() != ".csv":
            return None
        peek = strip_bom(p.read_text(encoding="utf-8", errors="replace")[:4096])
        pl = peek.lower()
        if ("fund name" in pl or "fundname" in pl) and (
            "scheme name" in pl or "schemedescription" in pl or "transaction type" in pl
        ):
            return DetectionResult(
                source_type="icici_direct_mf",
                confidence=0.91,
                account_hint=None,
                label=PARSER_LABELS["icici_direct_mf"],
            )
        return None

    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        return parse_icici_direct_mf_path(Path(path))
