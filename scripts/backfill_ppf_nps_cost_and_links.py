"""
One-time / occasional maintenance after improving PPF & NPS parsers.

1. **Dedupe NPS (CRA)** — multiple E/C/G rows with the same PRAN: repoint their
   ``investment_transactions`` onto one survivor (prefers
   ``National Pension System (NPS)``, else highest ``current_value``), then
   ``is_active=False`` on the extras.
2. Runs the orphan linker (PPF / NPS contribution rows → ``holding_id``).
3. **PPF** — ``principal_amount`` = sum(BUY) − sum(SELL) on linked txns.
4. **NPS** — ``principal_amount`` from linked **employee contribution** BUYs
   (notes contain ``nps employee contribution``); if none, falls back to all
   BUY − SELL (legacy unit-ledger imports).

Requires ``FERNET_KEY`` and a writable DB (same as the API). Example:

    python3 scripts/backfill_ppf_nps_cost_and_links.py --user-id sashank
    python3 scripts/backfill_ppf_nps_cost_and_links.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from api.database import get_engine, init_db
from api.models import Holding, InvestmentTransaction
from pipeline.holding_parsers.nps import NPS_CANONICAL_HOLDING_NAME, PLATFORM as NPS_CRA_PLATFORM
from pipeline.investment_txn_linking import link_unlinked_investment_transactions
from pipeline.models import AssetClass, InvestmentTxnType


def _ppf_principal_from_txns(session: Session, holding_id: int) -> float | None:
    rows = list(
        session.exec(
            select(InvestmentTransaction).where(InvestmentTransaction.holding_id == holding_id)
        ).all()
    )
    buy = sum(r.total_amount for r in rows if r.txn_type == InvestmentTxnType.BUY.value)
    sell = sum(r.total_amount for r in rows if r.txn_type == InvestmentTxnType.SELL.value)
    net = round(buy - sell, 2)
    return net if net > 0 else None


def _is_nps_employee_contribution_ledger(notes: str | None) -> bool:
    """True only for CRA contribution-table imports (not scheme NAV / billing rows)."""
    n = (notes or "").lower()
    if "nps employee contribution" not in n:
        return False
    if any(
        bad in n
        for bad in (
            "billing for",
            "switch out",
            "switch in",
            "unit redemption",
            "closing balance",
            "opening balance",
        )
    ):
        return False
    return "by contribution" in n or "by voluntary" in n or "voluntary contributions" in n


def _nps_principal_from_txns(session: Session, holding_id: int) -> float | None:
    rows = list(
        session.exec(
            select(InvestmentTransaction).where(InvestmentTransaction.holding_id == holding_id)
        ).all()
    )
    sell = sum(r.total_amount for r in rows if r.txn_type == InvestmentTxnType.SELL.value)
    buy_emp = sum(
        r.total_amount
        for r in rows
        if r.txn_type == InvestmentTxnType.BUY.value and _is_nps_employee_contribution_ledger(r.notes)
    )
    net = round(buy_emp - sell, 2)
    if net > 0:
        return net
    buy_all = sum(r.total_amount for r in rows if r.txn_type == InvestmentTxnType.BUY.value)
    net2 = round(buy_all - sell, 2)
    return net2 if net2 > 0 else None


def _pran_key(h: Holding) -> str:
    if h.folio_number_encrypted:
        return str(h.folio_number_encrypted).strip()
    if h.account_identifier_encrypted:
        return str(h.account_identifier_encrypted).strip()
    return f"_nopran_{h.id}"


def _dedupe_nps_holdings(session: Session, uid: str, dry_run: bool) -> int:
    """Return count of holdings deactivated."""
    nps = list(
        session.exec(
            select(Holding).where(
                Holding.user_id == uid,
                Holding.account_platform == NPS_CRA_PLATFORM,
                Holding.asset_class == AssetClass.NPS.value,
                Holding.is_active == True,  # noqa: E712
            )
        ).all()
    )
    by_pran: dict[str, list[Holding]] = defaultdict(list)
    for h in nps:
        by_pran[_pran_key(h)].append(h)

    n_deactivated = 0
    for pran_key, group in by_pran.items():
        if len(group) <= 1:
            continue
        canon = [h for h in group if h.name == NPS_CANONICAL_HOLDING_NAME]
        if canon:
            keep = max(canon, key=lambda h: (h.current_value or 0.0, h.id or 0))
        else:
            keep = max(group, key=lambda h: (h.current_value or 0.0, h.id or 0))
        if keep.id is None:
            continue
        for h in group:
            if h.id is None or h.id == keep.id:
                continue
            # Move any linked txns onto the survivor so principal math sees them.
            orphans = list(
                session.exec(
                    select(InvestmentTransaction).where(InvestmentTransaction.holding_id == h.id)
                ).all()
            )
            for t in orphans:
                print(
                    f"  Repoint investment_transaction id={t.id} "
                    f"holding_id {h.id} -> {keep.id} (NPS dedupe {pran_key=!r})"
                )
                if not dry_run:
                    t.holding_id = keep.id
                    session.add(t)
            print(f"Deactivate NPS holding id={h.id} name={h.name!r} ({pran_key=!r})")
            if not dry_run:
                h.is_active = False
                session.add(h)
            n_deactivated += 1
    return n_deactivated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-id",
        default=(os.environ.get("ARTH_USER_ID") or "sashank").strip(),
        help="Holdings owner (default: ARTH_USER_ID or sashank)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions only; no commits",
    )
    parser.add_argument(
        "--skip-nps-dedupe",
        action="store_true",
        help="Do not merge duplicate NPS rows (same PRAN)",
    )
    args = parser.parse_args()
    uid = args.user_id.strip()
    if not uid:
        raise SystemExit("user-id is empty")

    init_db()
    engine = get_engine()

    with Session(engine) as session:
        if not args.skip_nps_dedupe:
            nd = _dedupe_nps_holdings(session, uid, args.dry_run)
            print(f"NPS dedupe: deactivated {nd} duplicate row(s)")

        link_stats = link_unlinked_investment_transactions(session, user_ids=[uid])
        print(f"Investment txn link pass: {link_stats}")

        ppf_rows = list(
            session.exec(
                select(Holding).where(
                    Holding.user_id == uid,
                    Holding.asset_class == AssetClass.PPF.value,
                    Holding.is_active == True,  # noqa: E712
                )
            ).all()
        )

        for h in ppf_rows:
            if h.id is None:
                continue
            new_p = _ppf_principal_from_txns(session, h.id)
            old_p = h.principal_amount
            if new_p == old_p:
                continue
            print(f"PPF holding id={h.id} principal_amount {old_p!r} -> {new_p!r}")
            if not args.dry_run:
                h.principal_amount = new_p
                session.add(h)

        nps_rows = list(
            session.exec(
                select(Holding).where(
                    Holding.user_id == uid,
                    Holding.account_platform == NPS_CRA_PLATFORM,
                    Holding.asset_class == AssetClass.NPS.value,
                    Holding.is_active == True,  # noqa: E712
                )
            ).all()
        )

        for h in nps_rows:
            if h.id is None:
                continue
            new_p = _nps_principal_from_txns(session, h.id)
            old_p = h.principal_amount
            if new_p == old_p:
                continue
            print(f"NPS holding id={h.id} principal_amount {old_p!r} -> {new_p!r}")
            if not args.dry_run:
                h.principal_amount = new_p
                session.add(h)

        if args.dry_run:
            session.rollback()
            print("Dry-run: rolled back.")
        else:
            session.commit()
            print("Committed.")


if __name__ == "__main__":
    main()
