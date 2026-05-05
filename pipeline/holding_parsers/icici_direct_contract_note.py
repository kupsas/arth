"""
**Trades executed at NSE** equity PDFs from email (exchange-originated).

The scraper only ingests this one family: subject contains *Trades executed at NSE*;
password ``NSE_TRADES_EXECUTED_PASSWORD``. We do **not** parse ICICI contract notes or
order-confirmation PDFs here — keeps one code path and one email type.

We extract plain text / tables with ``pdfplumber``. Live NSE mail uses **Capital Market**
grids (``trade_details_*.pdf``): columns include Symbol, Buy/Sell as **B**/**S**, Trade No
(YYYYMMDD embedded), Quantity, Price — not the older one-line ``SYMBOL BUY qty rate`` text.
Symbol resolution uses :func:`resolve_icici_direct_nse_symbol` so ``holdings.symbol`` matches
NSE bhav keys like CSV imports (:mod:`pipeline.holding_parsers.icici_direct_equity`).
"""

from __future__ import annotations

import datetime
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pdfplumber

from api.services.price_feed import canonical_nse_symbol
from pipeline.detection import DetectionResult, PARSER_LABELS
from pipeline.holding_parsers.base import ParsedInvestmentTxn
from pipeline.holding_parsers.icici_direct_equity import resolve_icici_direct_nse_symbol
from pipeline.models import InvestmentTxnType

logger = logging.getLogger(__name__)

# Stored on each leg's ``metadata`` — only NSE trades PDFs are ingested from email.
KIND_NSE_EXECUTED = "nse_executed"

_ACCOUNT = "ICICI Direct"

# Trade / contract date in headers (DD-MM-YYYY or DD/MM/YYYY)
_TRADE_DATE_RES = (
    re.compile(
        r"(?:Trade\s*Date|Transaction\s*Date|Contract\s*Date|Note\s*Date|Date\s*of\s*contract)"
        r"[:\s]+(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{4})\b"),
)

# Extra header fields (best-effort; PDF layouts vary by year)
_NSE_HEADER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "nse_pdf_client_code",
        re.compile(r"(?:Client|Client\s+Code)\s*[:.]?\s*([A-Z0-9]{4,16})\b", re.IGNORECASE),
    ),
    ("nse_pdf_ucc", re.compile(r"\bUCC\s*[:.]?\s*([A-Z0-9]{6,12})\b", re.IGNORECASE)),
    (
        "nse_pdf_settlement_no",
        re.compile(r"Settlement\s*(?:No|Number|ID)?\s*[:.]?\s*([A-Z0-9/-]+)", re.IGNORECASE),
    ),
]

# NSE executed: data lines "SYMBOL BUY 10 123.45" (symbol must look like a ticker, not a header word)
_NSE_ROW = re.compile(
    r"^([A-Z][A-Z0-9.\-]{1,14})\s+(BUY|SELL)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)
_SKIP_SYMBOLS = frozenset(
    {
        "SYMBOL",
        "STOCK",
        "BUY",
        "SELL",
        "QTY",
        "QUANTITY",
        "PRICE",
        "RATE",
        "TOTAL",
        "VALUE",
        "NSE",
        "BSE",
        "MARKET",
    }
)


def _parse_dmY(s: str) -> datetime.date | None:
    s = s.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _extract_trade_date_from_text(text: str) -> datetime.date | None:
    """Best-effort trade date from the first page / header region (whole-doc text ok)."""
    head = text[:6000]
    for rx in _TRADE_DATE_RES:
        m = rx.search(head)
        if m:
            d = _parse_dmY(m.group(1))
            if d:
                return d
    return None


def _extract_nse_pdf_extras(text: str) -> dict[str, str]:
    """Pull client / UCC / settlement hints from the PDF header when present."""
    head = text[:8000]
    out: dict[str, str] = {}
    for key, rx in _NSE_HEADER_PATTERNS:
        m = rx.search(head)
        if m:
            val = m.group(1).strip()
            if val and len(val) <= 64:
                out[key] = val
    return out


def _extract_all_text(pdf_path: Path) -> str:
    parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
    return "\n".join(parts)


def _amount_from_in(s: str) -> float:
    return float(s.replace(",", "").strip() or 0.0)


def _merge_bucket_key(t: ParsedInvestmentTxn) -> tuple[datetime.date, str, str] | None:
    """Group key (date, txn_type, NSE symbol) for CSV-style aggregation.

    Rows without a resolvable symbol **and** no ISIN in metadata are not merged with
    anything else (caller keeps them as singleton legs).
    """
    meta = t.metadata or {}
    sym = (t.symbol or "").strip()
    if sym:
        cs = canonical_nse_symbol(sym)
        if cs:
            return (t.txn_date, t.txn_type, cs)
    isin = meta.get("isin")
    if isin:
        cs = resolve_icici_direct_nse_symbol(isin=str(isin).strip().upper(), icici_short="")
        if cs:
            return (t.txn_date, t.txn_type, cs)
    return None


def aggregate_icici_direct_trades(legs: list[ParsedInvestmentTxn]) -> list[ParsedInvestmentTxn]:
    """Merge split PDF lines into one row per (trade date, BUY/SELL, NSE symbol), like annual CSV.

    Sums ``quantity`` and ``total_amount``, then sets ``price_per_unit = total / qty``.
    Unmergeable legs (no symbol and no ISIN) pass through unchanged.
    """
    if not legs:
        return []
    buckets: dict[tuple[datetime.date, str, str], list[ParsedInvestmentTxn]] = defaultdict(list)
    orphans: list[ParsedInvestmentTxn] = []

    for t in legs:
        key = _merge_bucket_key(t)
        if key is None:
            orphans.append(t)
            logger.debug(
                "ICICI Direct: leg not merged (no symbol/ISIN): %s %s qty=%s",
                t.txn_date,
                t.txn_type,
                t.quantity,
            )
            continue
        buckets[key].append(t)

    out: list[ParsedInvestmentTxn] = []
    for key, group in buckets.items():
        qty = sum(x.quantity for x in group)
        total = sum(x.total_amount for x in group)
        if qty <= 0:
            continue
        ppu = total / qty
        first = group[0]
        meta = dict(first.metadata or {})
        meta["aggregated_from_legs"] = len(group)
        meta["aggregation"] = "icici_direct_pdf"
        d, txn_type, sym = key
        out.append(
            ParsedInvestmentTxn(
                txn_date=d,
                symbol=sym,
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


def _rows_from_tables(pdf_path: Path) -> list[list[str | None]]:
    """Flatten table cells for kinds that render as grids (esp. NSE executed)."""
    rows: list[list[str | None]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables() or []:
                for row in tbl:
                    rows.append(row)
    return rows


def _header_cell_norm(c: str | None) -> str:
    return re.sub(r"\s+", " ", (c or "").replace("\n", " ")).strip().lower()


def _is_nse_capital_market_header(row: list[str | None]) -> bool:
    """True if this row looks like the NSE *trade_details* / Capital Market column header."""
    parts = [_header_cell_norm(c) for c in row]
    joined = " ".join(parts)
    return (
        "symbol" in joined
        and "quantity" in joined
        and "buy" in joined
        and "sell" in joined
        and ("trade no" in joined.replace(" ", "") or "tradeno" in joined.replace(" ", ""))
    )


def _find_capital_market_columns(header_row: list[str | None]) -> dict[str, int] | None:
    """Map logical names to column indexes for NSE Capital Market trade tables."""
    col: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        h = _header_cell_norm(cell)
        if h == "symbol":
            col["symbol"] = i
        elif "buy" in h and "sell" in h:
            col["side"] = i
        elif h == "quantity":
            col["qty"] = i
        elif "price" in h and "traded" not in h:
            col["price"] = i
        elif "traded value" in h:
            col["value"] = i
        elif "trade" in h and "no" in h.replace(" ", ""):
            col["trade_no"] = i
        elif h == "client code":
            col["client_code"] = i
    need = ("symbol", "side", "qty", "price", "trade_no")
    if all(k in col for k in need):
        return col
    return None


def _date_from_nse_trade_no(raw: str | None) -> datetime.date | None:
    """NSE trade numbers embed the session date as the first 8 digits (YYYYMMDD)."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw).strip())
    if len(digits) < 8:
        return None
    try:
        y, mo, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def _txn_type_from_nse_bs(cell: str | None) -> str | None:
    """Capital Market PDFs use *B* / *S* in the Buy/Sell column."""
    s = (cell or "").strip().upper()
    if s in ("B", "BUY"):
        return InvestmentTxnType.BUY.value
    if s in ("S", "SELL"):
        return InvestmentTxnType.SELL.value
    return None


def _is_cm_data_row(row: list[str | None]) -> bool:
    if not row or not row[0]:
        return False
    first = (row[0] or "").strip().split()
    return bool(first) and first[0].isdigit()


def parse_nse_capital_market_tables(
    pdf_path: Path,
    *,
    doc_trade_date: datetime.date | None,
    fallback_trade_date: datetime.date | None,
    trade_date_source: str,
    extra_metadata: dict[str, Any] | None = None,
) -> list[ParsedInvestmentTxn]:
    """Parse *Capital Market* grid PDFs (``trade_details_*.pdf`` from nse-direct).

    Columns include **Symbol**, **Buy/Sell** as single-letter *B*/*S*, **Trade No** (date embedded),
    **Quantity**, **Price**, **Traded Value**. This is the layout live NSE emails use; the older
    ``SYMBOL BUY qty price`` single-line format is a fallback handled elsewhere.
    """
    extra_base = dict(extra_metadata or {})
    extra_base.setdefault("nse_pdf_layout", "capital_market_table")
    out: list[ParsedInvestmentTxn] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables() or []:
                if not tbl or len(tbl) < 2:
                    continue
                header_idx = 0
                if not _is_nse_capital_market_header(tbl[0]):
                    if len(tbl) > 1 and _is_nse_capital_market_header(tbl[1]):
                        header_idx = 1
                    else:
                        continue
                cols = _find_capital_market_columns(tbl[header_idx])
                if cols is None:
                    continue

                for row in tbl[header_idx + 1 :]:
                    if not row or not _is_cm_data_row(row):
                        continue
                    sym_raw = (row[cols["symbol"]] or "").strip()
                    if not sym_raw or sym_raw.upper() in _SKIP_SYMBOLS:
                        continue
                    txn_type = _txn_type_from_nse_bs(row[cols["side"]] if cols["side"] < len(row) else None)
                    if txn_type is None:
                        continue
                    qty = _amount_from_in(row[cols["qty"]] or "0")
                    price = _amount_from_in(row[cols["price"]] or "0")
                    if qty <= 0 or price <= 0:
                        continue
                    tno = row[cols["trade_no"]] if cols["trade_no"] < len(row) else None
                    row_date = _date_from_nse_trade_no(str(tno) if tno else None)
                    txn_date = row_date or doc_trade_date or fallback_trade_date
                    if txn_date is None:
                        logger.warning(
                            "NSE Capital Market row: could not infer trade date (symbol=%s)",
                            sym_raw,
                        )
                        continue
                    val_col = cols.get("value")
                    if val_col is not None and val_col < len(row) and (row[val_col] or "").strip():
                        total = abs(_amount_from_in(row[val_col] or "0"))
                    else:
                        total = abs(qty * price)

                    nse = resolve_icici_direct_nse_symbol(nse_from_pdf=sym_raw)
                    meta: dict[str, Any] = {
                        "source_pdf_kind": KIND_NSE_EXECUTED,
                        "nse_symbol_raw": sym_raw.upper(),
                        "trade_date_source": trade_date_source
                        if row_date is None
                        else "nse_trade_no",
                        "nse_trade_no": str(tno).strip() if tno else None,
                        **extra_base,
                    }
                    cc_idx = cols.get("client_code")
                    if cc_idx is not None and cc_idx < len(row) and (row[cc_idx] or "").strip():
                        meta["nse_row_client_code"] = str(row[cc_idx]).strip()

                    out.append(
                        ParsedInvestmentTxn(
                            txn_date=txn_date,
                            symbol=nse,
                            name=sym_raw.upper(),
                            txn_type=txn_type,
                            quantity=qty,
                            price_per_unit=price,
                            total_amount=round(total, 2),
                            account_platform=_ACCOUNT,
                            metadata=meta,
                        )
                    )
    return out


def parse_nse_executed_text(
    text: str,
    *,
    trade_date: datetime.date,
    trade_date_source: str = "unknown",
    extra_metadata: dict[str, Any] | None = None,
) -> list[ParsedInvestmentTxn]:
    """Parse *Trades executed at NSE* style plain text."""
    extra = dict(extra_metadata or {})
    out: list[ParsedInvestmentTxn] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("-"):
            continue
        m = _NSE_ROW.match(line)
        if not m:
            continue
        sym_raw, side, qty_s, rate_s = m.groups()
        if sym_raw.upper() in _SKIP_SYMBOLS:
            continue
        qty = _amount_from_in(qty_s)
        rate = _amount_from_in(rate_s)
        if qty <= 0 or rate <= 0:
            continue
        nse = resolve_icici_direct_nse_symbol(nse_from_pdf=sym_raw)
        total = abs(qty * rate)
        txn_type = (
            InvestmentTxnType.BUY.value if side.upper() == "BUY" else InvestmentTxnType.SELL.value
        )
        meta: dict[str, Any] = {
            "source_pdf_kind": KIND_NSE_EXECUTED,
            "nse_symbol_raw": sym_raw.upper(),
            "trade_date_source": trade_date_source,
            **extra,
        }
        out.append(
            ParsedInvestmentTxn(
                txn_date=trade_date,
                symbol=nse,
                name=sym_raw.upper(),
                txn_type=txn_type,
                quantity=qty,
                price_per_unit=rate,
                total_amount=total,
                account_platform=_ACCOUNT,
                metadata=meta,
            )
        )
    return out


def _parse_nse_executed_tables(
    table_rows: list[list[str | None]],
    *,
    trade_date: datetime.date,
    trade_date_source: str = "unknown",
    extra_metadata: dict[str, Any] | None = None,
) -> list[ParsedInvestmentTxn]:
    """When ``extract_text()`` is sparse, table cells may still list Symbol / side / qty / price."""
    extra = dict(extra_metadata or {})
    out: list[ParsedInvestmentTxn] = []
    for row in table_rows:
        cells = [(c or "").strip() for c in row]
        nonempty = [c for c in cells if c]
        if len(nonempty) < 4:
            continue
        sym, side, q1, q2 = nonempty[0], nonempty[1].upper(), nonempty[2], nonempty[3]
        if side not in ("BUY", "SELL") or sym.upper() in _SKIP_SYMBOLS:
            continue
        qty = _amount_from_in(q1)
        rate = _amount_from_in(q2)
        if qty <= 0 or rate <= 0:
            continue
        sym_u = sym.upper()
        nse = resolve_icici_direct_nse_symbol(nse_from_pdf=sym)
        txn_type = (
            InvestmentTxnType.BUY.value if side == "BUY" else InvestmentTxnType.SELL.value
        )
        meta: dict[str, Any] = {
            "source_pdf_kind": KIND_NSE_EXECUTED,
            "from_table": True,
            "nse_symbol_raw": sym_u,
            "trade_date_source": trade_date_source,
            **extra,
        }
        out.append(
            ParsedInvestmentTxn(
                txn_date=trade_date,
                symbol=nse,
                name=sym_u,
                txn_type=txn_type,
                quantity=qty,
                price_per_unit=rate,
                total_amount=abs(qty * rate),
                account_platform=_ACCOUNT,
                metadata=meta,
            )
        )
    return out


def parse_icici_direct_trade_pdf(
    pdf_path: Path,
    *,
    fallback_trade_date: datetime.date | None = None,
    aggregate: bool = True,
) -> list[ParsedInvestmentTxn]:
    """Open *pdf_path* (caller decrypts email attachment first) and return NSE trade legs.

    Args:
        pdf_path: Readable PDF path.
        fallback_trade_date: Gmail *received* date if the PDF header has no parseable date.
        aggregate: If True (default), merge split lines into one row per (date, side, NSE symbol).
            Set False when combining raw legs from several PDFs before one
            :func:`aggregate_icici_direct_trades` call.
    """
    text = _extract_all_text(pdf_path)
    trade_date_from_pdf = _extract_trade_date_from_text(text)
    doc_trade_date = trade_date_from_pdf or fallback_trade_date

    date_source = (
        "pdf_header"
        if trade_date_from_pdf
        else ("email_received_date" if fallback_trade_date else "unknown")
    )

    extras = _extract_nse_pdf_extras(text)
    extras["ingest_source"] = "nse_trades_executed_pdf"

    # Live NSE mail uses *Capital Market* grids (``trade_details_*.pdf``), not ``SYMBOL BUY …`` lines.
    rows = parse_nse_capital_market_tables(
        pdf_path,
        doc_trade_date=trade_date_from_pdf,
        fallback_trade_date=fallback_trade_date,
        trade_date_source=date_source,
        extra_metadata=extras,
    )
    if not rows and doc_trade_date is not None:
        rows = parse_nse_executed_text(
            text,
            trade_date=doc_trade_date,
            trade_date_source=date_source,
            extra_metadata=extras,
        )
    if not rows and doc_trade_date is not None:
        rows = _parse_nse_executed_tables(
            _rows_from_tables(pdf_path),
            trade_date=doc_trade_date,
            trade_date_source=date_source,
            extra_metadata=extras,
        )
    if not rows:
        logger.warning(
            "NSE trades PDF %s: no rows parsed (unknown layout?)",
            pdf_path.name,
        )

    if aggregate:
        rows = aggregate_icici_direct_trades(rows)
    return rows


def detect_icici_contract_note_pdf(path: str | Path) -> DetectionResult | None:
    """NSE / ICICI contract-note style PDF with Symbol × Buy/Sell × Quantity grid."""
    p = Path(path)
    if p.suffix.lower() != ".pdf" or not p.is_file():
        return None
    try:
        text = _extract_all_text(p)[:18_000]
    except Exception:
        return None
    tl = text.lower()
    # Distinctive vs equity transaction statement: mailers often say Trades executed / NSE.
    strong = (
        "trades executed" in tl
        or "capital market" in tl
        or ("contract" in tl and "nse" in tl)
        or ("trade no" in tl and "symbol" in tl)
    )
    if not strong:
        return None
    if "symbol" in tl and ("buy" in tl or "sell" in tl) and "quantity" in tl:
        return DetectionResult(
            source_type="icici_direct_contract_note",
            confidence=0.85,
            account_hint=None,
            label=PARSER_LABELS["icici_direct_contract_note"],
        )
    return None
