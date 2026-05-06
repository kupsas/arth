#!/usr/bin/env python3
"""
Compare one **ICICI Direct / NSE trade PDF** email against ``investment_transactions``.

Uses the same decrypt + parse path as the scraper (:mod:`parsers.statements.icici_direct_trade`).
Match key: ``txn_date``, ``symbol``, ``txn_type``, ``quantity``, ``total_amount`` (and
``account_platform == "ICICI Direct"``).

Examples::

    python3 scripts/validate_icici_direct_trade_email.py \\
        --query 'subject:\"Trades executed at NSE\"' \\
        --date-from 2025-01-01 --date-to 2025-12-31

    python3 scripts/validate_icici_direct_trade_email.py \\
        --message-id 18abc... \\
        --date-from 2025-06-01 --date-to 2025-06-30
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401 — loads ``.env``

from sqlmodel import Session, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import InvestmentTransaction  # noqa: E402
from parsers.holdings.icici_direct_contract_note import parse_icici_direct_trade_pdf
from parsers.statements.icici_direct_trade import (
    _nse_trades_pdf_password,
    classify_icici_direct_subject,
)
from scraper.gmail_client import GmailClient  # noqa: E402
from scraper.pdf_utils import decrypt_pdf  # noqa: E402


def _load_db_rows(
    session: Session,
    *,
    d0: dt.date,
    d1: dt.date,
) -> list[InvestmentTransaction]:
    stmt = (
        select(InvestmentTransaction)
        .where(InvestmentTransaction.account_platform == "ICICI Direct")
        .where(InvestmentTransaction.txn_date >= d0)
        .where(InvestmentTransaction.txn_date <= d1)
    )
    return list(session.exec(stmt).all())


def _match_key(r: InvestmentTransaction) -> tuple:
    return (
        r.txn_date,
        (r.symbol or "").upper(),
        r.txn_type,
        round(float(r.quantity), 6),
        round(float(r.total_amount), 2),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--message-id", help="Gmail message id")
    g.add_argument("--query", help="Gmail search (first hit); used with after:2015/01/01")
    ap.add_argument("--date-from", required=True, type=lambda s: dt.date.fromisoformat(s))
    ap.add_argument("--date-to", required=True, type=lambda s: dt.date.fromisoformat(s))
    args = ap.parse_args()

    init_db()
    client = GmailClient()
    client.authenticate()

    if args.message_id:
        msg = client.fetch_message_by_id(args.message_id)
    else:
        hits = client.search_messages(f"{args.query} after:2015/01/01", paginate=False, max_results_per_page=1)
        if not hits:
            print("No messages matched.")
            sys.exit(1)
        msg = hits[0]

    subject = msg.subject or ""
    if classify_icici_direct_subject(subject) is None:
        print(f"Subject does not look like a *Trades executed at NSE* email: {subject[:80]!r}")
        sys.exit(2)

    password, _ = _nse_trades_pdf_password()
    if not password:
        print("Missing password in environment for this email kind.")
        sys.exit(3)

    pdfs = client.get_attachments(msg.id)
    if not pdfs:
        print("No PDF attachments.")
        sys.exit(4)

    _name, raw = pdfs[0]
    path = decrypt_pdf(raw, password)
    try:
        parsed = parse_icici_direct_trade_pdf(
            path,
            fallback_trade_date=msg.received_at.date(),
        )
    finally:
        path.unlink(missing_ok=True)

    parsed_keys = {
        (
            t.txn_date,
            (t.symbol or "").upper(),
            t.txn_type,
            round(float(t.quantity), 6),
            round(float(t.total_amount), 2),
        )
        for t in parsed
    }

    with Session(get_engine()) as session:
        db_rows = _load_db_rows(session, d0=args.date_from, d1=args.date_to)
    db_keys = {_match_key(r) for r in db_rows}

    missing_in_db = parsed_keys - db_keys
    extra_in_db = db_keys - parsed_keys

    print(f"Message: {msg.id} | {subject[:70]}")
    print(f"Parsed legs: {len(parsed)}")
    print(f"DB rows (ICICI Direct in window): {len(db_rows)}")
    print(f"Matched keys (intersection): {len(parsed_keys & db_keys)}")
    if missing_in_db:
        print(f"Parsed but not in DB ({len(missing_in_db)}): {list(missing_in_db)[:12]}")
    if extra_in_db:
        print(f"In DB but not in this PDF ({len(extra_in_db)}): sample {list(extra_in_db)[:12]}")

    if not missing_in_db and not extra_in_db and parsed_keys:
        print("OK — full key-set match for this PDF vs DB window.")
    elif not parsed_keys:
        print("WARN — parser produced zero legs (check PDF layout).")


if __name__ == "__main__":
    main()
