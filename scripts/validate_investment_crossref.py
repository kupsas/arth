#!/usr/bin/env python3
"""
Cross-reference imported investment ledger rows against ICICI savings ``Transaction`` rows.

Read-only diagnostic (Phase A.2.8): surfaces unmatched bank debits/credits vs
brokerage imports so you can spot missing FY CSVs or settlement-date skew.

Usage (from repo root, after ingest on the same DB as ``APP_ENV``):

    APP_ENV=test python3 scripts/validate_investment_crossref.py
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

from sqlmodel import Session, select

from api.database import get_engine, init_db
from api.models import InvestmentTransaction, Transaction
from pipeline.models import TxnType

BANK_INVESTMENT_TYPES = (
    TxnType.EQUITY_PURCHASE.value,
    TxnType.MF_PURCHASE.value,
    TxnType.EQUITY_SALE.value,
    TxnType.MF_SALE.value,
)

PPF_KEYWORDS = ("PPF", "TO PPF", "PPF-")
NPS_KEYWORDS = ("NPS", "NPS-", "PROTEAN", "CRA")


def _within_days(a: dt.date, b: dt.date, n: int) -> bool:
    return abs((a - b).days) <= n


def _amount_close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + 1e-6


def load_bank_rows(session: Session, *, account_prefix: str | None) -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.txn_type.in_(BANK_INVESTMENT_TYPES))
    rows = list(session.exec(stmt).all())
    if account_prefix:
        rows = [r for r in rows if (r.account_id or "").startswith(account_prefix)]
    return rows


def load_inv_rows(session: Session) -> list[InvestmentTransaction]:
    return list(session.exec(select(InvestmentTransaction)).all())


def match_investments(
    bank: list[Transaction],
    inv: list[InvestmentTransaction],
    *,
    day_slack: int,
    amount_tol: float,
) -> tuple[list[tuple[Transaction, InvestmentTransaction]], list[Transaction], list[InvestmentTransaction]]:
    """Greedy 1:1 match on amount (±tol) and date (±day_slack)."""
    used_bank: set[int] = set()
    used_inv: set[int] = set()
    pairs: list[tuple[Transaction, InvestmentTransaction]] = []

    for b in bank:
        if b.id in used_bank:
            continue
        bamt = abs(float(b.amount))
        for it in inv:
            if it.id in used_inv:
                continue
            if not _amount_close(bamt, float(it.total_amount), amount_tol):
                continue
            if not _within_days(b.txn_date, it.txn_date, day_slack):
                continue
            pairs.append((b, it))
            used_bank.add(b.id)
            used_inv.add(it.id)
            break

    unmatched_bank = [b for b in bank if b.id not in used_bank]
    unmatched_inv = [i for i in inv if i.id not in used_inv]
    return pairs, unmatched_bank, unmatched_inv


def scan_ppf_nps_hints(session: Session) -> dict[str, list[Transaction]]:
    """PPF / NPS narration hints from raw bank descriptions (informational)."""
    stmt = select(Transaction).where(Transaction.direction == "OUTFLOW")
    rows = list(session.exec(stmt).all())
    out: dict[str, list[Transaction]] = defaultdict(list)
    for r in rows:
        raw = (r.raw_description or "").upper()
        if any(k in raw for k in PPF_KEYWORDS):
            out["ppf_hint"].append(r)
        if any(k.upper() in raw for k in NPS_KEYWORDS):
            out["nps_hint"].append(r)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Compare bank investment txns vs imported investment_transactions.")
    p.add_argument("--day-slack", type=int, default=1, help="Match if dates within ±N calendar days")
    p.add_argument("--amount-tol", type=float, default=50.0, help="Absolute INR tolerance on amounts")
    p.add_argument(
        "--account-prefix",
        default="ICICI",
        help="Only bank rows whose account_id starts with this (empty = all)",
    )
    args = p.parse_args()

    init_db()
    ap = (args.account_prefix or "").strip() or None

    with Session(get_engine()) as session:
        bank = load_bank_rows(session, account_prefix=ap)
        inv = load_inv_rows(session)
        pairs, ub, ui = match_investments(
            bank, inv, day_slack=args.day_slack, amount_tol=args.amount_tol
        )
        hints = scan_ppf_nps_hints(session)

    print("=== Investment cross-reference ===")
    print(f"Bank rows (filtered): {len(bank)}  Imported investment_transactions: {len(inv)}")
    print(f"Greedy matched pairs: {len(pairs)}")
    print(f"Unmatched bank (investment-classified): {len(ub)}")
    print(f"Unmatched imported ledger: {len(ui)}")
    print()

    if ub:
        print("--- Sample unmatched bank (up to 15) ---")
        for b in ub[:15]:
            print(
                f"  {b.txn_date}  {b.txn_type}  amt={b.amount:.2f}  "
                f"acct={b.account_id}  {b.raw_description[:100]!r}"
            )
        print()

    if ui:
        print("--- Sample unmatched investment_transactions (up to 15) ---")
        for it in ui[:15]:
            print(
                f"  {it.txn_date}  {it.txn_type}  amt={it.total_amount:.2f}  "
                f"{it.account_platform}  sym={it.symbol!r}"
            )
        print()

    print(f"PPF narration hints (OUTFLOW rows): {len(hints.get('ppf_hint', []))}")
    print(f"NPS narration hints (OUTFLOW rows): {len(hints.get('nps_hint', []))}")


if __name__ == "__main__":
    main()
