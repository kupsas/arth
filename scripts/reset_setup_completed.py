#!/usr/bin/env python3
"""
Clear ``app_users.setup_completed_at`` for one user so ``GET /api/setup/status`` can show
``needs_setup: true`` again (first-run / ``/setup`` gate is **installation-wide**: it stays
false while **any** user still has ``setup_completed_at`` set).

**Prerequisites**

- Use the same ``.env`` / env vars as the API (``APP_ENV``, ``ARTH_DB_PATH``, ``ARTH_DB_NAME``).
  **If ``ARTH_DB_PATH`` is set**, it wins over ``APP_ENV`` — always read the printed ``database_path``.

**Examples**

    APP_ENV=onboarding_test python3 scripts/reset_setup_completed.py --user Sashank --dry-run
    APP_ENV=onboarding_test python3 scripts/reset_setup_completed.py --user Sashank
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

import pipeline.config as pipeline_cfg  # noqa: F401 — loads ``.env`` and sets ``DB_PATH``

from sqlalchemy import func, update
from sqlmodel import Session, col, select

from api.database import get_engine, init_db
from api.models import AppUser


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", metavar="USERNAME", required=True, help="``app_users.username`` (login id).")
    ap.add_argument("--dry-run", action="store_true", help="Print JSON only; do not update.")
    args = ap.parse_args()
    username_input = str(args.user).strip()
    if not username_input:
        print("Error: --user must be non-empty", file=sys.stderr)
        sys.exit(2)

    init_db()
    engine = get_engine()

    with Session(engine) as session:
        others = session.exec(
            select(AppUser.username, AppUser.setup_completed_at).where(
                col(AppUser.setup_completed_at).is_not(None),
            )
        ).all()
        target = session.exec(
            select(AppUser).where(AppUser.username == username_input)
        ).first()
        if target is None:
            # Login names are stored as you typed at registration; try case-insensitive match.
            target = session.exec(
                select(AppUser).where(func.lower(AppUser.username) == username_input.lower())
            ).first()
        username = target.username if target else username_input

        payload: dict = {
            "app_env": os.environ.get("APP_ENV", ""),
            "arth_db_path_env_set": bool((os.environ.get("ARTH_DB_PATH") or "").strip()),
            "arth_db_name_env_set": bool((os.environ.get("ARTH_DB_NAME") or "").strip()),
            "database_path": str(pipeline_cfg.DB_PATH),
            "onboarding_db_expected": str((REPO_ROOT / "data" / "arth_onboarding.db").resolve()),
            "production_db_path": str((REPO_ROOT / "data" / "arth_main.db").resolve()),
            "target_user": username,
            "user_query": username_input,
            "target_found": target is not None,
            "target_setup_completed_at": target.setup_completed_at.isoformat()
            if target and target.setup_completed_at
            else None,
            "all_users_with_setup_completed": [
                {"username": u, "setup_completed_at": ts.isoformat() if ts else None}
                for u, ts in others
            ],
            "dry_run": args.dry_run,
        }
        print(json.dumps(payload, indent=2))

        if args.dry_run:
            return

        if target is None:
            print(json.dumps({"ok": False, "error": "user_not_found"}, indent=2), file=sys.stderr)
            sys.exit(1)

        session.exec(
            update(AppUser).where(col(AppUser.username) == username).values(setup_completed_at=None)
        )
        session.commit()

    print(json.dumps({"ok": True, "committed": True}, indent=2))


if __name__ == "__main__":
    main()
