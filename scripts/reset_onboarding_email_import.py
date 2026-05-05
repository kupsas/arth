#!/usr/bin/env python3
"""
Wipe **email-import onboarding data** so you can re-run Gmail backfill + classification.

**Idempotent:** safe to run repeatedly; later runs mostly delete 0 rows.

**What this touches (data only)**

1. ``investment_transactions`` — rows with ``source_type = 'email'`` (PPF PDF path, etc.).
2. ``transactions`` — rows for your user with ``source_type = 'email'`` (InstaAlerts / statement mails).
3. ``processed_emails`` — **entire table** (Gmail message-id dedup ledger). Required so the same
   messages are eligible again. This table has **no per-user column**; use only on a DB you own.
4. ``onboarding_states.backfill_progress_json`` — reset to ``{}`` for your user so chunk queues
   match an empty import.

**What this does *not* touch (configuration / setup you keep)**

- ``scraper_bank_senders``, ``scraper_account_mappings``, ``user_pipeline_sources``
- ``user_secrets``, ``app_users``, ``user_classification_settings``, merchant / contact tables
- ``onboarding_states`` row itself (no DELETE); other JSON columns (``discovery_results_json``,
  ``preclassification_raw_json``, ``completed_steps_json``, ``current_step``) unchanged unless
  you pass ``--set-current-step``.

**Prerequisites**

- Stop the API or avoid concurrent writes while this runs (SQLite + fewer locks).
- Load the right DB via ``.env`` (``ARTH_DB_PATH`` / ``ARTH_DB_NAME`` / ``APP_ENV``) — same as the app.

**Examples**

    # Preview counts (no writes)
    python3 scripts/reset_onboarding_email_import.py --user sashank --dry-run

    # Execute (put your auth username)
    python3 scripts/reset_onboarding_email_import.py --user sashank

    # Same user every time — add to .env: ARTH_RESET_ONBOARDING_EMAIL_IMPORT_USER=sashank
    python3 scripts/reset_onboarding_email_import.py

    # After reset, jump the wizard server step to the mail-import panel
    python3 scripts/reset_onboarding_email_import.py --user sashank --set-current-step backfill
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config as pipeline_cfg  # noqa: F401 — loads ``.env`` and resolves ``DB_PATH``

from sqlalchemy import delete
from sqlmodel import Session, col, func, select

from api.database import get_engine, init_db
from api.models import AppUser, InvestmentTransaction, OnboardingState, ProcessedEmail, Transaction


def _resolve_user_id(explicit: str | None) -> str:
    """Username from ``--user`` or ``ARTH_RESET_ONBOARDING_EMAIL_IMPORT_USER``."""
    u = (explicit or "").strip() or (os.environ.get("ARTH_RESET_ONBOARDING_EMAIL_IMPORT_USER") or "").strip()
    if not u:
        print(
            "Error: pass --user YOUR_ARTH_USER_ID or set ARTH_RESET_ONBOARDING_EMAIL_IMPORT_USER in .env",
            file=sys.stderr,
        )
        sys.exit(2)
    return u


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--user",
        metavar="USERNAME",
        default=None,
        help="Session / ``app_users.username`` (same as login). "
        "If omitted, uses env ARTH_RESET_ONBOARDING_EMAIL_IMPORT_USER.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts only; do not delete or update.",
    )
    ap.add_argument(
        "--set-current-step",
        metavar="STEP",
        default=None,
        help="If set, updates onboarding_states.current_step for this user (e.g. ``backfill``). "
        "Does not change completed_steps_json.",
    )
    args = ap.parse_args()

    user_id = _resolve_user_id(args.user)

    init_db()
    engine = get_engine()

    with Session(engine) as session:
        # Sanity-check: user row exists (optional but friendly for typos).
        exists = session.exec(select(AppUser.id).where(AppUser.username == user_id)).first()
        if exists is None:
            print(
                f"Warning: no row in app_users for username={user_id!r} — "
                "still proceeding (transactions may use this string as user_id).",
                file=sys.stderr,
            )

        inv_q = select(func.count()).select_from(InvestmentTransaction).where(
            col(InvestmentTransaction.source_type) == "email",
        )
        inv_n = int(session.exec(inv_q).one())

        txn_q = (
            select(func.count())
            .select_from(Transaction)
            .where(
                col(Transaction.user_id) == user_id,
                col(Transaction.source_type) == "email",
            )
        )
        txn_n = int(session.exec(txn_q).one())

        proc_q = select(func.count()).select_from(ProcessedEmail)
        proc_n = int(session.exec(proc_q).one())

        ob = session.exec(select(OnboardingState).where(OnboardingState.user_id == user_id)).first()
        ob_has = ob is not None
        bf_preview = (ob.backfill_progress_json or "{}")[:200] if ob else ""

        payload = {
            "database_path": str(pipeline_cfg.DB_PATH),
            "user_id": user_id,
            "would_delete_investment_transactions_email": inv_n,
            "would_delete_transactions_email_for_user": txn_n,
            "would_delete_processed_emails_all_rows": proc_n,
            "onboarding_state_row_exists": ob_has,
            "backfill_progress_json_prefix": bf_preview if ob_has else None,
            "dry_run": args.dry_run,
        }
        print(json.dumps(payload, indent=2))

        if args.dry_run:
            return

        # Order: children first — ``investment_transactions.bank_transaction_id`` may point at
        # ``transactions`` rows we are about to remove.
        if inv_n:
            session.exec(delete(InvestmentTransaction).where(col(InvestmentTransaction.source_type) == "email"))
        if txn_n:
            session.exec(
                delete(Transaction).where(
                    col(Transaction.user_id) == user_id,
                    col(Transaction.source_type) == "email",
                )
            )
        if proc_n:
            session.exec(delete(ProcessedEmail))

        if ob is not None:
            ob.backfill_progress_json = "{}"
            if args.set_current_step:
                ob.current_step = str(args.set_current_step).strip() or ob.current_step
            session.add(ob)
        elif args.set_current_step:
            print(
                "Warning: --set-current-step ignored (no onboarding_states row for this user).",
                file=sys.stderr,
            )

        session.commit()

    print(json.dumps({"ok": True, "committed": True}, indent=2))


if __name__ == "__main__":
    main()
