"""
ICICI Securities **Equity Transaction Statement** PDF (broker period statement).

These PDFs are **not** NSE *Trades executed at NSE* mailers and **not** annual CSV exports.
They render as table grids with ISIN, security name, B/S, quantities, and **gross** trade
consideration in the **Total (₹)** column (we use that for ``total_amount`` so it lines up
with annual CSV / NSE trade PDF ingest; **Net Amount** is optional metadata only).

We prefer ``pdfplumber`` ``extract_tables()`` because row alignment survives multi-line
company names better than raw ``extract_text()`` regex alone.

Parsed rows are merged with :func:`pipeline.holding_parsers.icici_direct_contract_note.aggregate_icici_direct_trades`
so ledger grain matches other ICICI Direct equity imports (one row per date × side × symbol).
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Sequence
from pathlib import Path

import pdfplumber

from pipeline.detection import DetectionResult, PARSER_LABELS
from pipeline.holding_parsers.base import ParsedInvestmentTxn
from pipeline.holding_parsers.icici_direct_contract_note import aggregate_icici_direct_trades
from pipeline.holding_parsers.icici_direct_equity import resolve_icici_direct_nse_symbol
from pipeline.holding_parsers.base import parse_icici_number
from pipeline.models import InvestmentTxnType

logger = logging.getLogger(__name__)

_ACCOUNT = "ICICI Direct"
_KIND = "icici_equity_transaction_statement_pdf"

# Indian equity ISIN is 12 characters: ``INE`` plus nine NSIN / check body characters
# (see ISO 6166; Indian listed stocks use the ``INE`` issuer prefix).
_ISIN = re.compile(r"^INE[A-Z0-9]{9}$")


def _norm_cell(c: str | None) -> str:
    return re.sub(r"\s+", " ", (c or "").replace("\n", " ")).strip()


def _parse_dmY_cell(raw: str | None) -> datetime.date | None:
    """First DD-MM-YYYY in a cell that may contain date + time."""
    s = _norm_cell(raw)
    if not s:
        return None
    m = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.date(y, mo, d)
        except ValueError:
            return None
    return None


def _map_equity_statement_columns(header_row: list[str | None]) -> dict[str, int] | None:
    """Locate columns by header keywords (layout varies slightly by statement era)."""
    col: dict[str, int] = {}
    for i, raw in enumerate(header_row):
        h = _norm_cell(raw).lower()
        if h == "isin" or h.startswith("isin "):
            col["isin"] = i
        elif "buy" in h and "sell" in h:
            col["side"] = i
        elif "quantity" in h:
            col["qty"] = i
        elif "gross rate" in h or ("rate" in h and "per" in h and "tax" not in h):
            col["gross_rate"] = i
        elif "net amount" in h:
            col["net"] = i
        # ``Total (₹)`` before brokerage/taxes — matches annual CSV / contract-note style DB rows.
        elif "total" in h and "net" not in h and "brokerage" not in h:
            col["total_gross"] = i
        elif h.startswith("security") or "security /" in h:
            col["security"] = i
        elif "settlement" in h and "date" in h and "settlement no" not in h.replace(" ", ""):
            col["settlement_date"] = i
        elif "trade" in h and "date" in h:
            col["trade_date"] = i

    need = ("isin", "side", "qty", "total_gross")
    if not all(k in col for k in need):
        return None
    return col


def _is_equity_statement_header_row(row: Sequence[str | None]) -> bool:
    parts = [_norm_cell(c).lower() for c in row]
    joined = " ".join(parts)
    return "isin" in joined and "buy" in joined and "sell" in joined


def _merged_header_row(r0: list[str | None], r1: list[str | None] | None) -> list[str | None]:
    """Join two header fragments (ICICI sometimes splits column titles across PDF rows)."""
    if not r1:
        return list(r0)
    out: list[str | None] = []
    n = max(len(r0), len(r1))
    for i in range(n):
        a = r0[i] if i < len(r0) else None
        b = r1[i] if i < len(r1) else None
        if a and b:
            out.append(f"{_norm_cell(a)} {_norm_cell(b)}")
        else:
            out.append(a or b)
    return out


def _txn_type_from_bs(cell: str | None) -> str | None:
    s = _norm_cell(cell).upper()
    if s in ("B", "BUY"):
        return InvestmentTxnType.BUY.value
    if s in ("S", "SELL"):
        return InvestmentTxnType.SELL.value
    return None


def _row_to_txn(
    row: list[str | None],
    cols: dict[str, int],
) -> ParsedInvestmentTxn | None:
    """Turn one table row into a leg, or return None if not a data row."""
    if not row or max(cols.values(), default=0) >= len(row):
        return None
    isin_raw = _norm_cell(row[cols["isin"]])
    if not _ISIN.match(isin_raw):
        return None

    side = _txn_type_from_bs(row[cols["side"]] if cols["side"] < len(row) else None)
    if side is None:
        return None

    qty = parse_icici_number((row[cols["qty"]] or "") if cols["qty"] < len(row) else "0")
    # Statement ``Total (₹)`` (gross consideration) — same grain as ICICI CSV / trade PDF ingest.
    gross_total = parse_icici_number(
        (row[cols["total_gross"]] or "") if cols["total_gross"] < len(row) else "0",
    )
    if qty <= 0 or abs(gross_total) <= 0:
        return None

    gross_rate = 0.0
    if cols.get("gross_rate") is not None and cols["gross_rate"] < len(row):
        gross_rate = parse_icici_number(row[cols["gross_rate"]] or "")

    # Prefer trade date for txn_date (matches annual CSV / NSE contract-note style).
    txn_date: datetime.date | None = None
    if cols.get("trade_date") is not None and cols["trade_date"] < len(row):
        txn_date = _parse_dmY_cell(row[cols["trade_date"]])
    if txn_date is None and cols.get("settlement_date") is not None:
        txn_date = _parse_dmY_cell(row[cols["settlement_date"]])
    if txn_date is None:
        logger.debug("ICICI equity stmt: skip row — no trade/settlement date (ISIN %s)", isin_raw)
        return None

    name_cell = ""
    sec_i = cols.get("security")
    if sec_i is not None and sec_i < len(row):
        name_cell = _norm_cell(row[sec_i])
    elif cols["isin"] + 1 < len(row):
        name_cell = _norm_cell(row[cols["isin"] + 1])
        if _ISIN.match(name_cell):
            name_cell = ""

    nse = resolve_icici_direct_nse_symbol(isin=isin_raw, icici_short="")
    ppu = gross_rate if gross_rate > 0 else (abs(gross_total) / qty if qty else 0.0)

    meta: dict = {
        "kind": _KIND,
        "isin": isin_raw,
        "source_layout": "icici_equity_transaction_statement_table",
    }
    if cols.get("net") is not None and cols["net"] < len(row):
        net_cell = parse_icici_number(row[cols["net"]] or "")
        if net_cell:
            meta["net_amount_statement"] = round(abs(net_cell), 2)

    return ParsedInvestmentTxn(
        txn_date=txn_date,
        symbol=nse,
        name=name_cell or isin_raw,
        txn_type=side,
        quantity=abs(qty),
        price_per_unit=round(abs(ppu), 6),
        total_amount=round(abs(gross_total), 2),
        account_platform=_ACCOUNT,
        metadata=meta,
    )


def _merge_split_contract_rows(rows: list[list[str | None]]) -> list[list[str | None]]:
    """Join rows where pdfplumber split ``ISEC/…`` contract ref across two physical rows."""
    if not rows:
        return rows
    out: list[list[str | None]] = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if i + 1 < len(rows):
            nxt = rows[i + 1]
            c0 = _norm_cell(r[0]) if r else ""
            n0 = _norm_cell(nxt[0]) if nxt else ""
            # First row is only ``ISEC/…`` fragment; second begins with settlement branch code.
            if c0.startswith("ISEC/") and n0.isdigit() and len(nxt) > 2:
                merged: list[str | None] = []
                max_len = max(len(r), len(nxt))
                for j in range(max_len):
                    a = r[j] if j < len(r) else None
                    b = nxt[j] if j < len(nxt) else None
                    if a and b:
                        merged.append(f"{_norm_cell(a)} {_norm_cell(b)}")
                    else:
                        merged.append(a or b)
                out.append(merged)
                i += 2
                continue
        out.append(r)
        i += 1
    return out


def _parse_tables_from_pdf(pdf_path: Path) -> list[ParsedInvestmentTxn]:
    raw_legs: list[ParsedInvestmentTxn] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables() or []:
                if not tbl or len(tbl) < 2:
                    continue
                header_idx: int | None = None
                merged_header: list[str | None] | None = None
                header_span = 1
                for hi, row in enumerate(tbl):
                    if not row:
                        continue
                    if _is_equity_statement_header_row(row):
                        header_idx = hi
                        merged_header = list(row)
                        header_span = 1
                        break
                    if hi + 1 < len(tbl):
                        merged = _merged_header_row(row, tbl[hi + 1])
                        if _is_equity_statement_header_row(merged):
                            header_idx = hi
                            merged_header = merged
                            header_span = 2
                            break
                if header_idx is None or merged_header is None:
                    continue
                cols = _map_equity_statement_columns(merged_header)
                if cols is None:
                    continue
                body = _merge_split_contract_rows(list(tbl[header_idx + header_span :]))
                for row in body:
                    leg = _row_to_txn(row, cols)
                    if leg:
                        raw_legs.append(leg)
    return raw_legs


def parse_icici_direct_equity_statement_pdf(
    pdf_path: str | Path,
    *,
    aggregate: bool = True,
) -> list[ParsedInvestmentTxn]:
    """
    Parse an ICICI Securities equity transaction statement PDF into investment legs.

    Args:
        pdf_path: Decrypted PDF path.
        aggregate: If True (default), merge legs by (date, NSE symbol, side) like other ICICI PDF paths.

    Returns:
        Parsed investment transactions with ``account_platform=\"ICICI Direct\"``.
    """
    path = Path(pdf_path)
    legs = _parse_tables_from_pdf(path)
    if not legs:
        logger.info("ICICI equity transaction statement: 0 legs from %s", path.name)
        return []
    if aggregate:
        return aggregate_icici_direct_trades(legs)
    return legs


def detect_icici_equity_statement_pdf(path: str | Path) -> DetectionResult | None:
    """Return a hit when PDF tables include ISIN + Buy/Sell equity-statement headers."""
    p = Path(path)
    if p.suffix.lower() != ".pdf" or not p.is_file():
        return None
    try:
        with pdfplumber.open(p) as pdf:
            for page in pdf.pages[:8]:
                tables = page.extract_tables() or []
                for tbl in tables:
                    for row in tbl[:22]:
                        if not row:
                            continue
                        cells = [str(c) if c is not None else "" for c in row]
                        if _is_equity_statement_header_row(cells):
                            return DetectionResult(
                                source_type="icici_direct_equity_statement_pdf",
                                confidence=0.87,
                                account_hint=None,
                                label=PARSER_LABELS["icici_direct_equity_statement_pdf"],
                            )
    except Exception:
        return None
    return None
