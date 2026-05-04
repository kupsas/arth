#!/usr/bin/env python3
"""
Phase 3 — Compare **ICICI Direct equity / MF account statement** PDFs to ``investment_transactions``.

This is the statement-PDF sibling of :mod:`scripts.compare_icici_trade_emails_to_db` (NSE trade
mailers). It reads **local decrypted PDFs** (for example from ``data/samples/icici_direct_*``),
runs the same parsers the email path uses, and checks rows already stored in your Arth DB.

**Equity (``ICICI Direct``)**  
  Match keys mirror the trade script: ``(txn_date, canonical NSE symbol, txn_type)``, then
  compare quantity / ``total_amount`` / ``price_per_unit`` with tolerances.

**Mutual funds (``ICICI Direct MF``)**  
  Statement rows often have ``symbol=None``; ingest stores the display name inside ``notes``. We
  match on date, type, amounts, and a **name-in-notes** check (same idea as
  :func:`pipeline.holding_pipeline.investment_txn_exists` for no-symbol txns), with float
  tolerances so tiny rounding differences do not false-fail.

Usage (from repo root, after ``pipeline.config`` can open your DB)::

    # One annual equity statement vs DB rows in the same trade window
    python3 scripts/validate_icici_direct_statements_vs_db.py \\
        --kind equity \\
        --pdf data/samples/icici_direct_equity/decrypted_...TRX-Equity....pdf \\
        --txn-date-from 2025-04-01 --txn-date-to 2026-03-31

    # MF account statement
    python3 scripts/validate_icici_direct_statements_vs_db.py \\
        --kind mf \\
        --pdf data/samples/icici_direct_mf/decrypted_...MFSTMT.pdf \\
        --txn-date-from 2025-04-01 --txn-date-to 2026-04-30

    # Auto-detect kind from filename (``TRX-Equity`` / ``MFSTMT`` / ``Mutual_Fund``)
    python3 scripts/validate_icici_direct_statements_vs_db.py \\
        --kind auto --pdf path/to/decrypted.pdf \\
        --txn-date-from 2025-01-01 --txn-date-to 2026-12-31

Exit code ``0`` only when there are no missing keys, no extra DB rows in the window, and no
amount mismatches on overlapping keys. Requires a readable SQLite DB (``ARTH_DB_PATH`` /
``APP_ENV`` per :mod:`pipeline.config`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401 — loads ``.env``, resolves DB path

from sqlmodel import Session, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import InvestmentTransaction  # noqa: E402
from api.services.price_feed import canonical_nse_symbol  # noqa: E402
from pipeline.holding_parsers.base import ParsedInvestmentTxn  # noqa: E402
from pipeline.holding_parsers.icici_direct_equity_statement_pdf import (  # noqa: E402
    parse_icici_direct_equity_statement_pdf,
)
from pipeline.holding_parsers.icici_direct_mf_statement_pdf import (  # noqa: E402
    parse_icici_direct_mf_statement_pdf,
)

EQUITY_PLATFORM = "ICICI Direct"
MF_PLATFORM = "ICICI Direct MF"


def _detect_kind(path: Path) -> str | None:
    """Infer ``equity`` / ``mf`` from filename patterns used in Gmail attachments."""
    name = path.name.lower()
    if "trx-equity" in name or "equity_transaction" in name:
        return "equity"
    if "mfstmt" in name or "mutual_fund" in name:
        return "mf"
    return None


def _match_key_equity(t: ParsedInvestmentTxn) -> tuple[dt.date, str, str]:
    return (
        t.txn_date,
        canonical_nse_symbol(t.symbol or ""),
        t.txn_type,
    )


def _close(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    if abs(a - b) <= abs_tol:
        return True
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= rel_tol


def _mf_amounts_close(p: ParsedInvestmentTxn, d: InvestmentTransaction, args: argparse.Namespace) -> bool:
    """Compare ledger floats with tolerances (MF statements often match ingest rounding)."""
    return (
        _close(float(p.quantity), float(d.quantity), args.tol_qty, args.tol_rel)
        and _close(float(p.total_amount), float(d.total_amount), args.tol_total, args.tol_rel)
        and _close(float(p.price_per_unit), float(d.price_per_unit), args.tol_ppu, args.tol_rel)
    )


def _mf_name_matches_notes(p: ParsedInvestmentTxn, notes: str | None) -> bool:
    """
    Mirror :func:`pipeline.holding_pipeline.investment_txn_exists` for ``symbol is None``:
    display name must appear inside combined ``notes`` (name + folio lines at ingest).
    """
    n = (p.name or "").strip()
    if not n:
        return False
    rn = (notes or "").strip()
    return n in rn or rn.endswith(n)


def _find_greedy_mf_match(
    p: ParsedInvestmentTxn,
    candidates: list[InvestmentTransaction],
    used: set[int],
    args: argparse.Namespace,
) -> InvestmentTransaction | None:
    """Pick first unmatched DB row that passes amount + name checks."""
    for d in candidates:
        if d.id is None or d.id in used:
            continue
        if not _mf_amounts_close(p, d, args):
            continue
        if not _mf_name_matches_notes(p, d.notes):
            continue
        return d
    return None


def _compare_equity(
    parsed: list[ParsedInvestmentTxn],
    db_rows: list[InvestmentTransaction],
    args: argparse.Namespace,
) -> tuple[list[str], bool]:
    """Same aggregation-key comparison as ``compare_icici_trade_emails_to_db``."""
    parsed_by_key = {_match_key_equity(t): t for t in parsed}
    db_by_key: dict[tuple, InvestmentTransaction] = {}
    dupes = 0
    for r in db_rows:
        k = _match_key_equity(
            ParsedInvestmentTxn(
                txn_date=r.txn_date,
                symbol=r.symbol,
                txn_type=r.txn_type,
                quantity=r.quantity,
                price_per_unit=r.price_per_unit,
                total_amount=r.total_amount,
                account_platform=r.account_platform,
            )
        )
        if k in db_by_key:
            dupes += 1
        db_by_key[k] = r

    lines: list[str] = []
    ok = True
    all_keys = set(parsed_by_key) | set(db_by_key)
    if dupes:
        lines.append(f"(warn) duplicate aggregate keys in DB for same window: {dupes}")

    for k in sorted(all_keys):
        p = parsed_by_key.get(k)
        d = db_by_key.get(k)
        if p is not None and d is not None:
            q_ok = _close(float(p.quantity), float(d.quantity), args.tol_qty, args.tol_rel)
            tot_ok = _close(float(p.total_amount), float(d.total_amount), args.tol_total, args.tol_rel)
            ppu_ok = _close(float(p.price_per_unit), float(d.price_per_unit), args.tol_ppu, args.tol_rel)
            if q_ok and tot_ok and ppu_ok:
                continue
            ok = False
            lines.append(
                f"MISMATCH {k[0]} {k[2]} {k[1]}\n"
                f"  parsed: qty={p.quantity} total={p.total_amount} ppu={p.price_per_unit}\n"
                f"  db:     qty={d.quantity} total={d.total_amount} ppu={d.price_per_unit}"
            )
        elif p is not None:
            ok = False
            lines.append(
                f"ONLY in PDF parse (no DB row for key): {k}  "
                f"qty={p.quantity} total={p.total_amount}"
            )
        else:
            ok = False
            lines.append(
                f"ONLY in DB (no parsed row for key): {k}  "
                f"qty={d.quantity} total={d.total_amount} id={d.id}"
            )

    return lines, ok


def _compare_mf(
    parsed: list[ParsedInvestmentTxn],
    db_rows: list[InvestmentTransaction],
    args: argparse.Namespace,
) -> tuple[list[str], bool]:
    """
    Greedy 1:1 match: each parsed row consumes at most one DB row with same calendar date,
    txn_type, and toleranced amounts + name embedded in notes.
    """
    lines: list[str] = []
    ok = True
    used_db: set[int] = set()

    # Index DB rows by date for faster scans (PDFs are usually small).
    by_date: dict[dt.date, list[InvestmentTransaction]] = {}
    for d in db_rows:
        by_date.setdefault(d.txn_date, []).append(d)

    missing: list[ParsedInvestmentTxn] = []
    for p in sorted(parsed, key=lambda x: (x.txn_date, x.txn_type, x.total_amount)):
        pool = by_date.get(p.txn_date, [])
        same_type = [d for d in pool if d.txn_type == p.txn_type]
        hit = _find_greedy_mf_match(p, same_type, used_db, args)
        if hit is None:
            missing.append(p)
            ok = False
        elif hit.id is not None:
            used_db.add(hit.id)

    for p in missing[:40]:
        lines.append(
            f"ONLY in PDF (no MF DB row): date={p.txn_date} type={p.txn_type} "
            f"qty={p.quantity} total={p.total_amount} name={p.name!r}"
        )
    if len(missing) > 40:
        lines.append(f"... and {len(missing) - 40} more PDF-only rows")

    for d in db_rows:
        if d.id is None or d.id in used_db:
            continue
        ok = False
        lines.append(
            f"ONLY in DB (unmatched MF row): id={d.id} date={d.txn_date} type={d.txn_type} "
            f"qty={d.quantity} total={d.total_amount} notes={d.notes!r}"
        )

    return lines, ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pdf",
        type=Path,
        action="append",
        required=True,
        help="Decrypted statement PDF path (repeat for multiple files).",
    )
    ap.add_argument(
        "--kind",
        choices=("equity", "mf", "auto"),
        default="auto",
        help="Statement type, or infer from filenames (default: auto).",
    )
    ap.add_argument(
        "--txn-date-from",
        type=lambda s: dt.date.fromisoformat(s),
        required=True,
    )
    ap.add_argument(
        "--txn-date-to",
        type=lambda s: dt.date.fromisoformat(s),
        required=True,
    )
    ap.add_argument("--tol-qty", type=float, default=0.0001)
    ap.add_argument("--tol-total", type=float, default=0.05)
    ap.add_argument("--tol-ppu", type=float, default=0.02)
    ap.add_argument("--tol-rel", type=float, default=0.0005)
    args = ap.parse_args()

    pdfs = [p.expanduser().resolve() for p in args.pdf]
    for p in pdfs:
        if not p.is_file():
            print(f"ERROR: PDF not found: {p}", file=sys.stderr)
            return 1

    kinds: list[str] = []
    for p in pdfs:
        if args.kind == "auto":
            k = _detect_kind(p)
            if k is None:
                print(
                    f"ERROR: --kind auto could not infer type from filename: {p.name}\n"
                    "  Pass --kind equity or --kind mf explicitly.",
                    file=sys.stderr,
                )
                return 1
            kinds.append(k)
        else:
            kinds.append(args.kind)

    if len(set(kinds)) != 1:
        print(
            "ERROR: Mixing equity and MF PDFs in one run is not supported. "
            "Run equity PDFs and MF PDFs separately.",
            file=sys.stderr,
        )
        return 1

    kind = kinds[0]
    parsed_all: list[ParsedInvestmentTxn] = []
    for p in pdfs:
        if kind == "equity":
            parsed_all.extend(parse_icici_direct_equity_statement_pdf(p))
        else:
            parsed_all.extend(parse_icici_direct_mf_statement_pdf(p))

    parsed = [t for t in parsed_all if args.txn_date_from <= t.txn_date <= args.txn_date_to]
    platform = EQUITY_PLATFORM if kind == "equity" else MF_PLATFORM

    init_db()
    with Session(get_engine()) as session:
        stmt = (
            select(InvestmentTransaction)
            .where(InvestmentTransaction.account_platform == platform)
            .where(InvestmentTransaction.txn_date >= args.txn_date_from)
            .where(InvestmentTransaction.txn_date <= args.txn_date_to)
        )
        db_rows = list(session.exec(stmt).all())

    print(
        f"Kind: {kind}  platform={platform}\n"
        f"PDF file(s): {len(pdfs)}\n"
        f"Parsed rows (in [{args.txn_date_from} .. {args.txn_date_to}]): {len(parsed)} "
        f"(from {len(parsed_all)} before window filter)\n"
        f"DB rows in window: {len(db_rows)}\n",
    )
    if parsed:
        p_dates = [t.txn_date for t in parsed]
        print(
            f"Coverage: parsed txn_date min/max = {min(p_dates)} / {max(p_dates)}  "
            f"({len(set(p_dates))} distinct day(s))\n"
        )
    if db_rows:
        d_dates = [r.txn_date for r in db_rows]
        print(
            f"Coverage: DB txn_date min/max in window = {min(d_dates)} / {max(d_dates)}  "
            f"({len(set(d_dates))} distinct day(s))\n"
        )

    if kind == "equity":
        diff_lines, ok = _compare_equity(parsed, db_rows, args)
    else:
        diff_lines, ok = _compare_mf(parsed, db_rows, args)

    if diff_lines:
        print("--- Issues ---")
        for line in diff_lines[:80]:
            print(line)
        if len(diff_lines) > 80:
            print(f"... and {len(diff_lines) - 80} more lines")
        print()

    if ok:
        print("OK — PDF aggregate keys line up with the database for this window (within tolerances).")
        return 0

    print("NOT OK — review differences above (missing ingest, extra DB rows, or amount drift).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
