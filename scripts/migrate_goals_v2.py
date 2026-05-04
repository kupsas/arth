"""
Goals architecture V2 — schema + data backfill (Sub-Plan A).

Run after pulling code that adds goal_class, tier L1–L4, earliest_liquidity_date, etc.
Idempotent: safe to run multiple times.

Usage:
    python scripts/migrate_goals_v2.py
    APP_ENV=test python scripts/migrate_goals_v2.py

Steps:
    1. init_db() — applies SQLite patches + creates inflation_rates table
    2. Remap legacy tier labels (VISION→L1, …) on goals
    3. Backfill goal_class / goal_subtype where NULL
    4. Backfill earliest_liquidity_date on holdings where NULL (V0 heuristics)
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlmodel import Session, select

from api.database import get_engine, init_db
from api.models import Goal, Holding
from pipeline.config import DB_PATH
from pipeline.models import AssetClass

# Legacy pyramid tier labels → L1–L4 (master plan).
_TIER_MAP = {
    "VISION": "L1",
    "STRATEGY": "L2",
    "TACTIC": "L3",
    "OPERATIONAL": "L4",
}

_FAR_FUTURE = datetime.date(2099, 12, 31)
_DEFAULT_GROWTH_HORIZON_YEARS = 10


def _add_business_days(start: datetime.date, n: int) -> datetime.date:
    """Add *n* business days (Mon–Fri); simple weekday skip (no holiday calendar)."""
    d = start
    added = 0
    while added < n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _compute_earliest_liquidity_v0(h: Holding, today: datetime.date) -> datetime.date | None:
    """Simplified liquidity date for migration; Sub-Plan C refines rules."""
    ac = (h.asset_class or "").strip().upper()
    mat = h.maturity_date

    if ac == AssetClass.SAVINGS.value:
        return today
    if ac in (
        AssetClass.EQUITY.value,
        AssetClass.MUTUAL_FUND.value,
        AssetClass.ESOP.value,
    ):
        return _add_business_days(today, 2)
    if ac == AssetClass.GOLD.value:
        return today
    if ac == AssetClass.SOVEREIGN_GOLD_BOND.value:
        return mat if mat else _FAR_FUTURE
    if ac == AssetClass.FD.value:
        if mat is not None and mat < today + datetime.timedelta(days=7):
            return mat
        return today + datetime.timedelta(days=7)
    if ac == AssetClass.PPF.value:
        return mat if mat else _FAR_FUTURE
    if ac == AssetClass.NPS.value:
        # User DOB not wired in V0 — maturity_date or sentinel.
        return mat if mat else _FAR_FUTURE
    if ac in (AssetClass.REAL_ESTATE.value, AssetClass.OTHER.value):
        return mat if mat else _FAR_FUTURE
    return mat if mat else _FAR_FUTURE


def _goal_class_from_type(goal_type: str) -> str:
    gt = goal_type.strip().upper()
    if gt == "EXPENSE_LIMIT":
        return "RECURRING_CASH_FLOW"
    if gt == "INVESTMENT":
        return "POINT_IN_TIME"
    # SAVINGS, EMERGENCY_FUND, DEBT_PAYOFF, TAX, INSURANCE, …
    return "POINT_IN_TIME"


def _goal_subtype_from_type(goal_type: str) -> str:
    gt = goal_type.strip().upper()
    if gt == "EMERGENCY_FUND":
        return "EMERGENCY_FUND"
    if gt == "DEBT_PAYOFF":
        return "LOAN_PAYOFF"
    return "CUSTOM"


def run_migration() -> None:
    print("\nArth DB Migration — Goals V2 (Sub-Plan A)")
    print(f"Target DB: {DB_PATH}")

    init_db()
    engine = get_engine()
    today = datetime.datetime.now(datetime.UTC).date()

    with Session(engine) as session:
        goals = session.exec(select(Goal)).all()
        tier_updates = 0
        growth_to_pit = 0
        for g in goals:
            t = (g.tier or "").strip().upper()
            if t in _TIER_MAP:
                g.tier = _TIER_MAP[t]
                tier_updates += 1
            if g.goal_class is None:
                g.goal_class = _goal_class_from_type(g.goal_type)
            if (g.goal_class or "").strip().upper() == "GROWTH":
                g.goal_class = "POINT_IN_TIME"
                growth_to_pit += 1
                if g.target_date is None:
                    g.target_date = today + datetime.timedelta(
                        days=365 * _DEFAULT_GROWTH_HORIZON_YEARS
                    )
            if g.goal_subtype is None:
                g.goal_subtype = _goal_subtype_from_type(g.goal_type)
            session.add(g)

        holdings = session.exec(select(Holding)).all()
        liq_updates = 0
        for h in holdings:
            if h.earliest_liquidity_date is None:
                h.earliest_liquidity_date = _compute_earliest_liquidity_v0(h, today)
                liq_updates += 1
            session.add(h)

        session.commit()
        print(f"  ✓ Tier labels migrated (rows touched): {tier_updates}")
        print(f"  ✓ goal_class / goal_subtype backfilled for {len(goals)} goals")
        if growth_to_pit:
            print(f"  ✓ GROWTH → POINT_IN_TIME (with default horizon if needed): {growth_to_pit} goals")
        print(f"  ✓ earliest_liquidity_date set for {liq_updates} holdings (was NULL)")

    print("\nMigration complete.\n")


if __name__ == "__main__":
    run_migration()
