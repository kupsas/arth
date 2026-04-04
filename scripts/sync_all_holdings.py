#!/usr/bin/env python3
"""
One-time backfill: re-link orphan investment transactions, auto-create holdings
for ICICI Direct BUY/SIP orphans, then recompute every holding from ledger history.

Usage::

    python3 scripts/sync_all_holdings.py
    python3 scripts/sync_all_holdings.py --user-id sashank

Requires ``ARTH_USER_ID`` (or ``--user-id``) for the auto-create step when a txn
has no holding and needs a new row (ICICI Direct equity / MF only).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Project root on path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlmodel import Session, col, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import Holding, InvestmentTransaction  # noqa: E402
from api.services.holdings_sync import (  # noqa: E402
    ensure_holding_for_transaction,
    sync_holdings_for_user,
)
from pipeline.investment_txn_linking import link_unlinked_investment_transactions  # noqa: E402

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--user-id",
        default=None,
        help="Default owner for orphan auto-create (default: ARTH_USER_ID or sashank)",
    )
    args = p.parse_args(argv)

    uid_default = (args.user_id or os.environ.get("ARTH_USER_ID") or "sashank").strip() or "sashank"

    init_db()
    engine = get_engine()

    with Session(engine) as session:
        stats_link = link_unlinked_investment_transactions(session)
        session.commit()
        logger.info(
            "link_unlinked_investment_transactions: examined=%s linked=%s still_orphan=%s ambiguous=%s",
            stats_link["examined"],
            stats_link["linked"],
            stats_link["still_orphan"],
            stats_link["ambiguous"],
        )

        orphans = list(
            session.exec(
                select(InvestmentTransaction).where(
                    col(InvestmentTransaction.holding_id).is_(None)
                )
            ).all()
        )
        auto_created = 0
        for txn in orphans:
            hid = ensure_holding_for_transaction(session, txn, user_id=uid_default)
            if hid is not None:
                auto_created += 1
        session.commit()
        logger.info(
            "ensure_holding_for_transaction: still_orphan_before=%s auto_linked_or_created=%s",
            len(orphans),
            auto_created,
        )

        user_rows = session.exec(select(Holding.user_id).distinct()).all()
        user_ids = sorted({str(u) for u in user_rows})
        total_synced = 0
        for uid in user_ids:
            out = sync_holdings_for_user(session, uid)
            total_synced += int(out.get("holdings_examined", 0))
        session.commit()
        logger.info("sync_holdings_for_user: holdings_processed=%s users=%s", total_synced, user_ids)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
