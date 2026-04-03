"""
ICICI Direct equity: portfolio summary (holdings) + annual trade CSVs (investment txns).

``Current_Portfolio_Txns.csv`` is intentionally ignored (fill-level; redundant).
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from pipeline.holding_parsers.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    parse_icici_number,
    strip_bom,
)
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod

# ISIN → NSE trading symbol (extend as you add names). Yahoo is not used for marks.
ISIN_TO_NSE_SYMBOL: dict[str, str] = {
    "INE646L01027": "INDIGO",  # InterGlobe Aviation
    "INE040A01034": "HDFCBANK",
    "INE263A01024": "BEL",  # Bharat Electronics (ICICI code BHAELE — not BHEL)
    "INE257A01026": "BHEL",  # Bharat Heavy Electricals — distinct from BEL
    "INE438A01022": "APOLLOTYRE",  # Apollo Tyres
    "INE213A01029": "IOC",  # Indian Oil
    "INE531A01024": "KANSAINER",  # Kansai Nerolac Paints
    "INE018A01030": "LT",  # Larsen & Toubro
    "INE002A01018": "RELIANCE",  # Reliance Industries
    "INE245A01021": "TATAPOWER",  # Tata Power
    "INE397D01024": "ZENSARTECH",
    "INE528G01035": "MINDACORP",
    "INE383A01012": "STOONE",  # Stone India — halted; symbol kept for identity
}

# Broker short codes (summary + annual trades). Keep keys aligned with
# ``_ICICI_BROKER_TO_NSE`` in ``api.services.price_feed`` so legacy rows still refresh.
ICICI_SHORT_TO_NSE: dict[str, str] = {
    "INTAVI": "INDIGO",
    "BAFINS": "BAJAJFINSV",
    "BANMAH": "MAHABANK",
    "BHAWIR": "BHARTIARTL",
    "HDFBAN": "HDFCBANK",
    # ICICI uses BHAELE for Bharat *Electronics* (NSE BEL). Do not map to BHEL (Heavy Electricals).
    "BHAELE": "BEL",
    "APOTYR": "APOLLOTYRE",
    "COCSHI": "COCHINSHIP",
    "ENGIND": "ENGINERSIN",
    "HDFAMC": "HDFCAMC",
    "ICINIF": "NIFTYIETF",
    "INDOIL": "IOC",
    "INTBUI": "INTERARCH",
    "INTDES": "INTELLECT",
    "KANNER": "KANSAINER",
    "LARTOU": "LT",
    "MAHGAS": "MGL",
    "NAGCON": "NCC",
    "NRBBEA": "NRBBEARING",
    "PHOMIL": "PHOENIXLTD",
    "PRAIN": "PRAJIND",
    "PVRLIM": "PVRINOX",
    "RELIND": "RELIANCE",
    "SANEN": "SANSERA",
    "SHRTRA": "SHRIRAMFIN",
    "SKFIND": "SKFINDIA",
    "TATMOT": "TATAMOTORS",
    "TATPOW": "TATAPOWER",
    "VEDLIM": "VEDL",
    "WHIIND": "WHIRLPOOL",
    "ZENSAR": "ZENSARTECH",
    "MINDAC": "MINDACORP",
    "STOONE": "STOONE",
}

SUMMARY_FILENAME = "Current_Portfolio_Summary.csv"
SKIP_TXN_FILES = {"Current_Portfolio_Txns.csv"}


def _row_get(row: dict[str, str | None], *candidates: str) -> str:
    """Match CSV columns after stripping BOM/whitespace from header keys."""
    key_map = {strip_bom((k or "").strip()): v for k, v in row.items()}
    for c in candidates:
        if c in key_map and key_map[c] is not None:
            return str(key_map[c])
    return ""


def _resolve_nse_symbol(*, isin: str | None, icici_short: str) -> str:
    from pipeline.icici_symbol_overrides import merge_with_disk

    iso_map = merge_with_disk(ISIN_TO_NSE_SYMBOL, "isin_to_nse")
    short_map = merge_with_disk(ICICI_SHORT_TO_NSE, "icici_short_to_nse")
    u = icici_short.strip().upper()
    if isin:
        iso = isin.strip().upper()
        if iso in iso_map:
            return iso_map[iso]
    return short_map.get(u, u)


def resolve_icici_direct_nse_symbol(
    *,
    isin: str | None = None,
    icici_short: str = "",
    nse_from_pdf: str | None = None,
) -> str:
    """Pick the DB/NSE bhav symbol for an equity leg (email PDFs + CSV ingest).

    **Priority:** explicit NSE ticker from a PDF column (e.g. *Trades executed at NSE*)
    wins; else ISIN → :data:`ISIN_TO_NSE_SYMBOL`; else ICICI stock code →
    :data:`ICICI_SHORT_TO_NSE`; else pass through uppercased broker code.

    Keeps holdings price refresh aligned with :func:`api.services.price_feed.canonical_nse_symbol`.
    """
    raw = (nse_from_pdf or "").strip().upper()
    if raw:
        for suf in (".NS", ".NSE", ".BO"):
            if raw.endswith(suf):
                raw = raw[: -len(suf)]
                break
        return raw
    return _resolve_nse_symbol(isin=isin, icici_short=icici_short)


def parse_portfolio_summary_csv(path: Path) -> tuple[list[ParsedHolding], dict[str, str]]:
    """Return holdings and a map ISIN (upper) → NSE symbol for trade enrichment."""
    holdings: list[ParsedHolding] = []
    isin_to_nse: dict[str, str] = {}
    text = strip_bom(path.read_text(encoding="utf-8", errors="replace"))
    reader = csv.DictReader(line.strip() for line in text.splitlines() if line.strip())
    if not reader.fieldnames:
        return holdings, isin_to_nse

    for row in reader:
        stock = _row_get(row, "Stock Symbol", "StockSymbol").strip()
        company = _row_get(row, "Company Name", "CompanyName").strip()
        isin = _row_get(row, "ISIN Code", "ISIN").strip().upper() or None
        qty_s = _row_get(row, "Qty", "Quantity", "QTY")
        avg_s = _row_get(row, "Average Cost Price", "AverageCostPrice")
        mkt_s = _row_get(row, "Current Market Price", "CurrentMarketPrice")
        val_cost_s = _row_get(row, "Value At Cost", "ValueAtCost")
        val_mkt_s = _row_get(row, "Value At Market Price", "ValueAtMarketPrice")

        if not company and not stock:
            continue

        nse = _resolve_nse_symbol(isin=isin, icici_short=stock)
        if isin:
            isin_to_nse[isin] = nse

        qty = parse_icici_number(qty_s)
        avg = parse_icici_number(avg_s)
        cur_px = parse_icici_number(mkt_s)
        val_cost = parse_icici_number(val_cost_s)
        val_mkt = parse_icici_number(val_mkt_s)

        is_stoone = "STOONE" in stock.upper() or "STONE INDIA" in company.upper()
        valuation = ValuationMethod.MANUAL.value if is_stoone else ValuationMethod.MARKET_PRICE.value
        notes = None
        if is_stoone:
            notes = "SEBI trading halt — status TBD; MANUAL valuation (no reliable market price)."

        holdings.append(
            ParsedHolding(
                symbol=nse,
                isin=isin,
                name=company or stock,
                quantity=qty if qty else None,
                asset_class=AssetClass.EQUITY.value,
                valuation_method=valuation,
                account_platform="ICICI Direct",
                average_cost_per_unit=avg if avg else None,
                current_price_per_unit=cur_px if cur_px else None,
                current_value=val_mkt if val_mkt else val_cost or None,
                liquidity_class=LiquidityClass.T_PLUS_1.value,
                notes=notes,
                metadata={"source_file": path.name, "icici_stock_symbol": stock},
            )
        )
    return holdings, isin_to_nse


def _is_trade_csv_header(fieldnames: list[str] | None) -> bool:
    if not fieldnames:
        return False
    joined = " ".join(strip_bom(f or "") for f in fieldnames).lower()
    return "trade value" in joined and "stock" in joined and "action" in joined


def parse_annual_trade_csv(path: Path, isin_to_nse: dict[str, str]) -> list[ParsedInvestmentTxn]:
    """One annual FY export: order-level rows with weighted average price."""
    out: list[ParsedInvestmentTxn] = []
    text = strip_bom(path.read_text(encoding="utf-8", errors="replace"))
    reader = csv.DictReader(line.strip() for line in text.splitlines() if line.strip())
    if not reader.fieldnames or not _is_trade_csv_header(list(reader.fieldnames)):
        return out

    for row in reader:
        date_s = _row_get(row, "Date").strip()
        stock = _row_get(row, "Stock").strip()
        action = _row_get(row, "Action").strip()
        qty = parse_icici_number(_row_get(row, "Qty"))
        price = parse_icici_number(_row_get(row, "Price"))
        trade_val = parse_icici_number(_row_get(row, "Trade Value", "TradeValue"))

        if not date_s or not action:
            continue

        try:
            dt = datetime.strptime(date_s, "%d-%b-%Y").date()
        except ValueError:
            try:
                dt = datetime.strptime(date_s, "%d-%m-%Y").date()
            except ValueError:
                continue

        isin_key = _row_get(row, "ISIN Code", "ISIN").strip().upper()
        sym = _resolve_nse_symbol(isin=isin_key if isin_key else None, icici_short=stock)
        # Portfolio summary map (same folder) can add ISIN→NSE not yet on disk
        if isin_key and isin_key in isin_to_nse:
            sym = isin_to_nse[isin_key]

        if action.lower() == "buy":
            txn_type = InvestmentTxnType.BUY.value
        elif action.lower() == "sell":
            txn_type = InvestmentTxnType.SELL.value
        else:
            continue

        total = abs(trade_val) if trade_val else abs(qty * price)
        ppu = price if price else (total / qty if qty else 0.0)

        out.append(
            ParsedInvestmentTxn(
                txn_date=dt,
                symbol=sym,
                name=stock,
                txn_type=txn_type,
                quantity=abs(qty),
                price_per_unit=abs(ppu),
                total_amount=abs(total),
                account_platform="ICICI Direct",
                metadata={"source_file": path.name, "icici_stock_symbol": stock},
            )
        )
    return out


def parse_icici_direct_equity_dir(
    directory: Path,
) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    """Load ``Current_Portfolio_Summary.csv`` + every trade-style ``*.csv`` except txns dump."""
    d = directory.resolve()
    holdings: list[ParsedHolding] = []
    txns: list[ParsedInvestmentTxn] = []
    isin_map: dict[str, str] = {}

    summary = d / SUMMARY_FILENAME
    if summary.is_file():
        h, iso = parse_portfolio_summary_csv(summary)
        holdings.extend(h)
        isin_map.update(iso)

    for p in sorted(d.glob("*.csv")):
        name = p.name
        if name == SUMMARY_FILENAME or name in SKIP_TXN_FILES:
            continue
        # Only merge files that look like annual trade exports
        peek = strip_bom(p.read_text(encoding="utf-8", errors="replace")[:4096])
        first_line = peek.splitlines()[0] if peek else ""
        if "Trade Value" not in first_line and "Trade Value" not in peek:
            continue
        txns.extend(parse_annual_trade_csv(p, isin_map))

    txns.sort(key=lambda t: t.txn_date)
    return holdings, txns


class ICICIDirectEquityParser(BaseHoldingParser):
    @property
    def source_id(self) -> str:
        return "icici_direct_equity"

    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        p = Path(path)
        if p.is_dir():
            return parse_icici_direct_equity_dir(p)
        if p.is_file():
            if p.name == SUMMARY_FILENAME:
                h, _ = parse_portfolio_summary_csv(p)
                return h, []
            return [], parse_annual_trade_csv(p, {})
        return [], []
