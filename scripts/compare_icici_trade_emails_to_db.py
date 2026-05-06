#!/usr/bin/env python3
"""
Compare **ICICI Direct / NSE trade PDFs** from Gmail against ``investment_transactions``.

1. Downloads PDFs in the Gmail **received** window, parses raw legs, then applies
   :func:`parsers.holdings.icici_direct_contract_note.aggregate_icici_direct_trades`
   **once** over all legs (same as production: one CSV-like row per date / side / symbol).

2. Loads DB rows for ``account_platform == \"ICICI Direct\"`` with ``txn_date`` in the
   **trade** window.

3. Matches on ``(txn_date, canonical NSE symbol, txn_type)`` and compares **quantity**,
   **total_amount**, **price_per_unit** with tolerances (float / rounding noise).

``InvestmentTransaction`` rows do **not** use ``content_hash`` (that is only on bank
:class:`~api.models.Transaction`); dedupe for inserts is equality on the natural key in
:func:`pipeline.holding_pipeline.investment_txn_exists`.

Usage::

    python3 scripts/compare_icici_trade_emails_to_db.py \\
        --after 2025/09/01 --before 2026/01/01 \\
        --txn-date-from 2025-09-01 --txn-date-to 2025-12-31
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401

from sqlmodel import Session, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import InvestmentTransaction  # noqa: E402
from api.services.price_feed import canonical_nse_symbol  # noqa: E402
from parsers.holdings.icici_direct_contract_note import (  # noqa: E402
    aggregate_icici_direct_trades,
    parse_icici_direct_trade_pdf,
)
from parsers.statements.icici_direct_trade import (  # noqa: E402
    _nse_trades_pdf_password,
    classify_icici_direct_subject,
)
from scraper.gmail_client import GmailClient  # noqa: E402
from scraper.pdf_utils import decrypt_pdf  # noqa: E402


def _match_key(
    *,
    txn_date: dt.date,
    symbol: str | None,
    txn_type: str,
) -> tuple[dt.date, str, str]:
    return (txn_date, canonical_nse_symbol(symbol or ""), txn_type)


def _close(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    if abs(a - b) <= abs_tol:
        return True
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= rel_tol


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--after", default="2023/04/01", help="Gmail after: (received)")
    ap.add_argument("--before", default="2023/07/01", help="Gmail before: (exclusive)")
    ap.add_argument(
        "--txn-date-from",
        type=lambda s: dt.date.fromisoformat(s),
        default=dt.date(2023, 4, 1),
    )
    ap.add_argument(
        "--txn-date-to",
        type=lambda s: dt.date.fromisoformat(s),
        default=dt.date(2023, 6, 30),
    )
    ap.add_argument(
        "--tol-qty",
        type=float,
        default=0.0001,
        help="Absolute tolerance for quantity comparison.",
    )
    ap.add_argument(
        "--tol-total",
        type=float,
        default=0.05,
        help="Absolute tolerance for total_amount (INR).",
    )
    ap.add_argument(
        "--tol-ppu",
        type=float,
        default=0.02,
        help="Absolute tolerance for price_per_unit.",
    )
    ap.add_argument(
        "--tol-rel",
        type=float,
        default=0.0005,
        help="Relative tolerance (fraction) when abs tol is not enough.",
    )
    ap.add_argument(
        "--query",
        default=(
            "(from:ebix@nse.co.in OR from:nseinvest@nse.co.in OR from:nse-direct@nse.co.in) "
            '"Trades executed at NSE"'
        ),
        help="Gmail search fragment",
    )
    args = ap.parse_args()

    init_db()
    client = GmailClient()
    client.authenticate()

    full_query = f"{args.query} after:{args.after} before:{args.before}"
    messages = client.search_messages(
        full_query,
        paginate=True,
        max_results_per_page=100,
    )
    print(f"Gmail: {len(messages)} message(s)\n  {full_query[:220]}{'…' if len(full_query) > 220 else ''}\n")

    raw_legs: list = []
    parse_errors: list[str] = []

    for msg in sorted(messages, key=lambda m: m.received_at):
        if classify_icici_direct_subject(msg.subject or "") is None:
            continue
        pwd, env = _nse_trades_pdf_password()
        if not pwd:
            parse_errors.append(f"[{msg.id}] missing {env}")
            continue
        pdfs = client.get_attachments(msg.id)
        if not pdfs:
            parse_errors.append(f"[{msg.id}] no PDF")
            continue
        for _fn, pdf_bytes in pdfs:
            path = decrypt_pdf(pdf_bytes, pwd)
            try:
                legs = parse_icici_direct_trade_pdf(
                    path,
                    fallback_trade_date=msg.received_at.date(),
                    aggregate=False,
                )
                for t in legs:
                    if args.txn_date_from <= t.txn_date <= args.txn_date_to:
                        raw_legs.append(t)
            except Exception as exc:
                parse_errors.append(f"[{msg.id}] {exc!r}")
            finally:
                path.unlink(missing_ok=True)

    # One aggregate pass over **all** raw legs from every PDF (same as merging multi-line PDF splits).
    parsed_agg = aggregate_icici_direct_trades(raw_legs)
    parsed_by_key = {
        _match_key(txn_date=t.txn_date, symbol=t.symbol, txn_type=t.txn_type): t
        for t in parsed_agg
    }

    with Session(get_engine()) as session:
        stmt = (
            select(InvestmentTransaction)
            .where(InvestmentTransaction.account_platform == "ICICI Direct")
            .where(InvestmentTransaction.txn_date >= args.txn_date_from)
            .where(InvestmentTransaction.txn_date <= args.txn_date_to)
        )
        db_rows = list(session.exec(stmt).all())

    db_by_key: dict[tuple, InvestmentTransaction] = {}
    db_dupes = 0
    for r in db_rows:
        k = _match_key(txn_date=r.txn_date, symbol=r.symbol, txn_type=r.txn_type)
        if k in db_by_key:
            db_dupes += 1
        db_by_key[k] = r

    all_keys = set(parsed_by_key) | set(db_by_key)
    print(
        f"Trade-date window: {args.txn_date_from} … {args.txn_date_to}\n"
        f"Raw PDF legs (in window): {len(raw_legs)}  →  aggregated keys: {len(parsed_by_key)}\n"
        f"DB rows (ICICI Direct):    {len(db_rows)}  →  distinct match keys: {len(db_by_key)}"
        + (f"  (duplicate keys in DB: {db_dupes})" if db_dupes else "")
        + "\n"
    )

    major_diffs: list[str] = []
    ok_count = 0

    for k in sorted(all_keys):
        p = parsed_by_key.get(k)
        d = db_by_key.get(k)
        if p is not None and d is not None:
            q_ok = _close(
                float(p.quantity),
                float(d.quantity),
                args.tol_qty,
                args.tol_rel,
            )
            tot_ok = _close(
                float(p.total_amount),
                float(d.total_amount),
                args.tol_total,
                args.tol_rel,
            )
            ppu_ok = _close(
                float(p.price_per_unit),
                float(d.price_per_unit),
                args.tol_ppu,
                args.tol_rel,
            )
            if q_ok and tot_ok and ppu_ok:
                ok_count += 1
            else:
                major_diffs.append(
                    f"MISMATCH {k[0]} {k[2]} {k[1]}\n"
                    f"  parsed: qty={p.quantity} total={p.total_amount} ppu={p.price_per_unit}\n"
                    f"  db:     qty={d.quantity} total={d.total_amount} ppu={d.price_per_unit}\n"
                    f"  flags:  qty_ok={q_ok} total_ok={tot_ok} ppu_ok={ppu_ok}"
                )
        elif p is not None:
            major_diffs.append(
                f"ONLY in Gmail parse (no DB row for this key): {k}  "
                f"qty={p.quantity} total={p.total_amount} ppu={p.price_per_unit}"
            )
        else:
            major_diffs.append(
                f"ONLY in DB (no parsed aggregate for this key): {k}  "
                f"qty={d.quantity} total={d.total_amount} ppu={d.price_per_unit} id={d.id}"
            )

    print(f"Matched keys with qty/total/ppu within tolerance: {ok_count} / {len(all_keys)}\n")

    if major_diffs:
        print("--- Differences (review) ---")
        for line in major_diffs[:60]:
            print(line)
            print()
        if len(major_diffs) > 60:
            print(f"... and {len(major_diffs) - 60} more\n")
    else:
        print("OK — every (date, symbol, side) key lines up within tolerances.\n")

    if parse_errors:
        print(f"Parse / env issues ({len(parse_errors)}):")
        for e in parse_errors[:20]:
            print(f"  {e}")


if __name__ == "__main__":
    main()
