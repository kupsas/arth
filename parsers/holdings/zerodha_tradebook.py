"""
Zerodha Console **tradebook** CSV export (user upload backup path).

Columns: ``symbol, isin, trade_date, exchange, segment, series, trade_type, auction,
quantity, price, trade_id, order_id, order_execution_time``.

Email demat statements are the source of truth; this path fills gaps when mail is missing.
Rows aggregate to one line per (trade_date, BUY/SELL, symbol) for dedupe alignment.
"""

from __future__ import annotations

import csv
import datetime
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pipeline.detection import DetectionResult, PARSER_LABELS
from parsers.holdings.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    strip_bom,
)
from parsers.holdings.derived_equity import derive_equity_holdings
from pipeline.models import InvestmentTxnType

_ACCOUNT = "Zerodha"
_KIND = "zerodha_tradebook_csv"

# Indian ISIN (equities INE*, ETFs INF*, etc.).
_ISIN_RE = re.compile(r"^IN[A-Z][A-Z0-9]{9}$")

_REQUIRED_HEADER_TOKENS = frozenset(
    {
        "symbol",
        "isin",
        "trade_date",
        "trade_type",
        "quantity",
        "price",
        "order_execution_time",
    }
)


def _row_get(row: dict[str, str | None], *candidates: str) -> str:
    key_map = {strip_bom((k or "").strip()).lower(): v for k, v in row.items()}
    for c in candidates:
        ck = c.lower()
        if ck in key_map and key_map[ck] is not None:
            return str(key_map[ck])
    return ""


def _parse_trade_date(raw: str) -> date | None:
    s = (raw or "").strip()[:10]
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _txn_type_from_trade_type(cell: str) -> str | None:
    s = (cell or "").strip().lower()
    if s in ("buy", "b"):
        return InvestmentTxnType.BUY.value
    if s in ("sell", "s"):
        return InvestmentTxnType.SELL.value
    return None


def _merge_bucket_key(t: ParsedInvestmentTxn) -> tuple[date, str, str] | None:
    # Deferred import: ``api.services.price_feed`` imports the holdings package,
    # so importing it at module top-level would create a circular import.
    from api.services.price_feed import canonical_nse_symbol

    sym = (t.symbol or "").strip()
    if sym:
        cs = canonical_nse_symbol(sym)
        if cs:
            return (t.txn_date, t.txn_type, cs)
        return (t.txn_date, t.txn_type, sym.upper())
    meta = t.metadata or {}
    isin = str(meta.get("isin") or "").strip().upper()
    if _ISIN_RE.match(isin):
        return (t.txn_date, t.txn_type, isin)
    return None


def aggregate_zerodha_trades(legs: list[ParsedInvestmentTxn]) -> list[ParsedInvestmentTxn]:
    """Merge split tradebook legs into one row per (date, side, symbol or ISIN)."""
    if not legs:
        return []
    buckets: dict[tuple[date, str, str], list[ParsedInvestmentTxn]] = defaultdict(list)
    orphans: list[ParsedInvestmentTxn] = []
    for t in legs:
        key = _merge_bucket_key(t)
        if key is None:
            orphans.append(t)
            continue
        buckets[key].append(t)

    out: list[ParsedInvestmentTxn] = []
    for key, group in buckets.items():
        qty = sum(x.quantity for x in group)
        total = sum(x.total_amount for x in group)
        if qty <= 0:
            continue
        ppu = total / qty if qty else 0.0
        first = group[0]
        meta = dict(first.metadata or {})
        meta["aggregated_from_legs"] = len(group)
        meta["aggregation"] = _KIND
        d, txn_type, sym = key
        out.append(
            ParsedInvestmentTxn(
                txn_date=d,
                symbol=sym if not sym.startswith("IN") else first.symbol,
                name=first.name,
                txn_type=txn_type,
                quantity=round(qty, 6),
                price_per_unit=round(ppu, 6),
                total_amount=round(total, 2),
                account_platform=first.account_platform,
                notes=first.notes,
                metadata=meta,
            )
        )
    out.extend(orphans)
    return out


def _parse_tradebook_csv(path: Path) -> list[ParsedInvestmentTxn]:
    # Deferred import: avoids the ``api.services.price_feed`` ↔ holdings circular import.
    from api.services.price_feed import canonical_nse_symbol

    text = strip_bom(path.read_text(encoding="utf-8", errors="replace"))
    reader = csv.DictReader(line.strip() for line in text.splitlines() if line.strip())
    if not reader.fieldnames:
        return []

    headers = {strip_bom(h or "").strip().lower() for h in reader.fieldnames}
    if not _REQUIRED_HEADER_TOKENS.issubset(headers):
        return []

    out: list[ParsedInvestmentTxn] = []
    for row in reader:
        sym = _row_get(row, "symbol").strip().upper()
        isin_raw = _row_get(row, "isin").strip().upper()
        if not sym and not _ISIN_RE.match(isin_raw):
            continue

        txn_date = _parse_trade_date(_row_get(row, "trade_date"))
        if txn_date is None:
            continue

        side = _txn_type_from_trade_type(_row_get(row, "trade_type"))
        if side is None:
            continue

        try:
            qty = abs(float(_row_get(row, "quantity") or "0"))
            price = abs(float(_row_get(row, "price") or "0"))
        except ValueError:
            continue
        if qty <= 0:
            continue

        total = round(qty * price, 2)
        nse = canonical_nse_symbol(sym) if sym else None

        out.append(
            ParsedInvestmentTxn(
                txn_date=txn_date,
                symbol=nse or sym or None,
                name=sym or isin_raw,
                txn_type=side,
                quantity=qty,
                price_per_unit=round(price, 6),
                total_amount=total,
                account_platform=_ACCOUNT,
                metadata={
                    "kind": _KIND,
                    "price_source": "statement",
                    "isin": isin_raw if _ISIN_RE.match(isin_raw) else None,
                    "exchange": _row_get(row, "exchange").strip(),
                    "segment": _row_get(row, "segment").strip(),
                    "series": _row_get(row, "series").strip(),
                    "trade_id": _row_get(row, "trade_id").strip(),
                    "order_id": _row_get(row, "order_id").strip(),
                    "order_execution_time": _row_get(row, "order_execution_time").strip(),
                    "source_file": path.name,
                },
            )
        )
    return out


def parse_zerodha_tradebook_path(
    path: Path,
    *,
    aggregate: bool = True,
) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    txns: list[ParsedInvestmentTxn] = []
    p = path.resolve()
    if p.is_file():
        txns.extend(_parse_tradebook_csv(p))
    elif p.is_dir():
        for f in sorted(p.glob("*.csv")):
            txns.extend(_parse_tradebook_csv(f))
    if aggregate:
        txns = aggregate_zerodha_trades(txns)
    txns.sort(key=lambda t: t.txn_date)
    holdings = derive_equity_holdings(txns, platform=_ACCOUNT)
    return holdings, txns


class ZerodhaTradebookParser(BaseHoldingParser):
    """Zerodha Console tradebook CSV (backup when email demat statement is unavailable)."""

    @property
    def source_id(self) -> str:
        return "zerodha_tradebook"

    @classmethod
    def detect(cls, path: str | Path) -> DetectionResult | None:
        p = Path(path)
        if p.is_dir():
            for f in sorted(p.glob("*.csv")):
                hit = cls.detect(f)
                if hit:
                    return hit
            return None
        if p.suffix.lower() != ".csv":
            return None
        peek = strip_bom(p.read_text(encoding="utf-8", errors="replace")[:4096]).lower()
        if "symbol" in peek and "trade_date" in peek and "order_execution_time" in peek:
            return DetectionResult(
                source_type="zerodha_tradebook",
                confidence=0.92,
                account_hint=None,
                label=PARSER_LABELS["zerodha_tradebook"],
            )
        return None

    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        return parse_zerodha_tradebook_path(Path(path))


def _header_tokens(row: Sequence[str | None]) -> set[str]:
    return {strip_bom(str(c or "")).strip().lower() for c in row if c}


__all__ = [
    "ZerodhaTradebookParser",
    "aggregate_zerodha_trades",
    "parse_zerodha_tradebook_path",
]
