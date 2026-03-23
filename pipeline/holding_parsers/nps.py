"""
NPS account statements: multi-section text/CSV hybrid.

PRAN is PII — stored encrypted on ``Holding.folio_number_encrypted`` at ingest.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path

from pipeline.holding_parsers.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    parse_indian_amount,
    strip_bom,
)
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod

PLATFORM = "NPS (CRA)"


def _parse_stmt_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _clean_pran_from_text(block: str) -> str | None:
    m = re.search(r"PRAN\D*['\s]?(\d{12})\b", block, re.I)
    if m:
        return m.group(1)
    m2 = re.search(r"\b(\d{12})\b", block)
    if m2 and "PRAN" in block.upper():
        return m2.group(1)
    return None


def _try_cra_scheme_wise_row(ln: str, pran: str | None, fname: str) -> ParsedHolding | None:
    """CRA export: ``...SCHEME E - TIER I,value,units,nav`` (see Scheme Wise Summary)."""
    up = ln.upper()
    if "SCHEME E" not in up and "SCHEME C" not in up and "SCHEME G" not in up:
        return None
    if "TIER" not in up:
        return None
    if "PARTICULARS" in up and "SCHEME WISE" in up:
        return None
    try:
        row = next(csv.reader(io.StringIO(ln)))
    except (StopIteration, csv.Error):
        return None
    # Trailing commas (common in CRA exports) add an empty cell — without this,
    # row[:-3] wrongly treats the Rupee value as part of the scheme name.
    row = [c.strip() for c in row]
    while row and row[-1] == "":
        row.pop()
    if len(row) < 4:
        return None
    head = row[0].strip()
    if head.lower().startswith("particulars"):
        return None
    # Contribution / switch rows sometimes mention "SCHEME" in the description — not a balance line.
    if _parse_stmt_date(head):
        return None
    try:
        value = parse_indian_amount(row[-3])
        units = parse_indian_amount(row[-2])
        nav = parse_indian_amount(row[-1])
    except ValueError:
        return None
    if units <= 0 and value <= 0:
        return None
    name = ",".join(row[:-3]).strip()
    if not name:
        return None
    nav_eff = (value / units) if units > 0 else (nav or 0.0)
    return ParsedHolding(
        symbol=None,
        name=name[:512],
        quantity=units if units > 0 else None,
        asset_class=AssetClass.NPS.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        account_platform=PLATFORM,
        current_value=value if value > 0 else None,
        current_price_per_unit=nav_eff if nav_eff else None,
        liquidity_class=LiquidityClass.ILLIQUID.value,
        folio_number=pran,
        metadata={"source_file": fname, "pran": pran or ""},
    )


def parse_nps_statement(path: Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    text = strip_bom(path.read_text(encoding="utf-8", errors="replace"))
    lines = [ln.rstrip() for ln in text.splitlines()]

    head = "\n".join(lines[:40])
    pran = _clean_pran_from_text(head)

    holdings: list[ParsedHolding] = []
    txns: list[ParsedInvestmentTxn] = []

    # Scheme-wise summary (CRA): long scheme name + value, units, NAV.
    for ln in lines:
        ph = _try_cra_scheme_wise_row(ln, pran, path.name)
        if ph:
            holdings.append(ph)
            continue
        # Legacy/minimal fixtures: first column E / C / G only.
        parts = [p.strip() for p in re.split(r",|\t", ln) if p.strip() != ""]
        if len(parts) < 4:
            continue
        tag = parts[0].upper()
        if tag not in ("E", "C", "G"):
            continue
        try:
            value = parse_indian_amount(parts[1])
            units = parse_indian_amount(parts[2])
            nav = parse_indian_amount(parts[3])
        except ValueError:
            continue
        if value <= 0 and units <= 0:
            continue
        nav_eff = (value / units) if units > 0 else (nav or 0.0)
        holdings.append(
            ParsedHolding(
                symbol=None,
                name=f"NPS Scheme {tag}",
                quantity=units if units > 0 else None,
                asset_class=AssetClass.NPS.value,
                valuation_method=ValuationMethod.MARKET_PRICE.value,
                account_platform=PLATFORM,
                current_value=value if value > 0 else None,
                current_price_per_unit=nav_eff if nav_eff else None,
                liquidity_class=LiquidityClass.ILLIQUID.value,
                folio_number=pran,
                metadata={"source_file": path.name, "pran": pran or ""},
            )
        )

    current_scheme = ""
    for ln in lines:
        if re.match(r"^\s*tier\s+", ln, re.I):
            current_scheme = ln.strip()
        if re.match(r"^\s*scheme\s+[ECG]\b", ln, re.I):
            current_scheme = ln.strip()
        # CRA: full line title before each scheme's transaction block.
        if (
            re.search(r"SCHEME [ECG]", ln, re.I)
            and "TIER" in ln.upper()
            and "," not in ln
            and len(ln.strip()) < 220
        ):
            current_scheme = ln.strip()

        try:
            reader = csv.reader(io.StringIO(ln))
            row = next(reader)
        except (csv.Error, StopIteration):
            continue
        row = [c.strip() for c in row]
        if len(row) < 3:
            continue
        d0 = _parse_stmt_date(row[0])
        if not d0:
            continue
        desc = row[1]
        desc_l = desc.lower()
        if "opening" in desc_l or "closing" in desc_l:
            continue

        amt_raw = row[2]
        neg_paren = amt_raw.strip().startswith("(") and amt_raw.strip().endswith(")")
        try:
            amt = parse_indian_amount(amt_raw.replace("(", "").replace(")", ""))
            nav = parse_indian_amount(row[3]) if len(row) > 3 else 0.0
            units = parse_indian_amount(row[4]) if len(row) > 4 else 0.0
        except ValueError:
            # Not a numeric txn row (e.g. bank name merged into CSV columns).
            continue
        if neg_paren:
            amt = -abs(amt)

        if "switch" in desc_l and "in" in desc_l:
            txn_type = InvestmentTxnType.SWITCH_IN.value
        elif "switch" in desc_l and "out" in desc_l:
            txn_type = InvestmentTxnType.SWITCH_OUT.value
        elif amt < 0 or "charge" in desc_l or "fee" in desc_l:
            txn_type = InvestmentTxnType.SELL.value
        else:
            txn_type = InvestmentTxnType.BUY.value

        qty = abs(units) if units else 1.0
        ppu = abs(nav) if nav else (abs(amt) / qty if qty else abs(amt))

        txns.append(
            ParsedInvestmentTxn(
                txn_date=d0,
                symbol=None,
                name=f"{current_scheme or 'NPS'} — {desc}".strip(" —"),
                txn_type=txn_type,
                quantity=qty,
                price_per_unit=ppu,
                total_amount=abs(amt),
                account_platform=PLATFORM,
                notes=desc,
                metadata={"source_file": path.name, "pran": pran or "", "scheme_line": current_scheme},
            )
        )

    txns.sort(key=lambda t: t.txn_date)

    # Scheme summary rows can repeat across sections; keep last snapshot per scheme name.
    by_name: dict[str, ParsedHolding] = {}
    for h in holdings:
        by_name[h.name] = h
    holdings = list(by_name.values())

    return holdings, txns


class NPSParser(BaseHoldingParser):
    @property
    def source_id(self) -> str:
        return "nps"

    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        p = Path(path)
        all_h: list[ParsedHolding] = []
        all_t: list[ParsedInvestmentTxn] = []
        if p.is_file():
            h, t = parse_nps_statement(p)
            return h, t
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if f.suffix.lower() == ".csv":
                    h, t = parse_nps_statement(f)
                    all_h.extend(h)
                    all_t.extend(t)
        return all_h, all_t
