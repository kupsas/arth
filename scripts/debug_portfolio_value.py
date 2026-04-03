"""
Diagnostic script: compare the two portfolio valuation code paths.

Path A ("headline"): as_of_date=None → reads h.current_value from DB
Path B ("trend"):    as_of_date=given → historical replay / price lookup

Run from repo root (set your DB user id — do not hardcode personal ids in git):

    export ARTH_DEBUG_USER_ID=<your_holdings_user_id>
    python -m scripts.debug_portfolio_value
"""

from __future__ import annotations

import datetime
import os
import sys
from collections import defaultdict
from pathlib import Path

# Ensure repo root is on sys.path so imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session

from api.database import get_engine, init_db
from api.services.net_worth import (
    _active_holdings,
    _holding_value,
    _historical_total_assets,
    compute_net_worth,
)
from api.services.historical_portfolio import (
    historical_market_assets_value,
    historical_market_holding_value,
    historical_nps_holding_value,
    historical_ppf_holding_value,
    is_market_replay_holding,
    market_position_quantities_as_of,
    _latest_price_on_or_before,
)


# Holdings user id from the DB — required via env so this script stays shareable / safe to commit.
USER_ID = os.environ.get("ARTH_DEBUG_USER_ID", "").strip()
AS_OF = datetime.date(2026, 3, 27)  # comparison date; change if you need another day


def fmt(v: float) -> str:
    """Format a number as Indian-style with commas."""
    return f"₹{v:>14,.2f}"


def run_diagnosis(session: Session) -> None:
    if not USER_ID:
        print(
            "Missing ARTH_DEBUG_USER_ID. Example: export ARTH_DEBUG_USER_ID=your_db_user_id",
            file=sys.stderr,
        )
        raise SystemExit(1)
    holdings = _active_holdings(session, USER_ID)
    print(f"Active holdings for '{USER_ID}': {len(holdings)}")
    print(f"Diagnosis date: {AS_OF}")
    print("=" * 100)

    # ── Path A: Headline (as_of_date=None) ─────────────────────────
    print("\n📊 PATH A — HEADLINE (as_of_date=None, uses h.current_value)")
    print("-" * 100)

    headline_by_class: dict[str, float] = defaultdict(float)
    headline_by_holding: list[tuple[str, str, str, float]] = []

    for h in holdings:
        val = _holding_value(session, h, None)  # returns h.current_value
        headline_by_class[h.asset_class] += val
        headline_by_holding.append((h.asset_class, h.name, h.symbol or "—", val))

    headline_total = sum(headline_by_class.values())
    print(f"\n  {'Asset Class':<30} {'Value':>16}")
    print(f"  {'—' * 30} {'—' * 16}")
    for ac in sorted(headline_by_class, key=lambda k: -headline_by_class[k]):
        print(f"  {ac:<30} {fmt(headline_by_class[ac])}")
    print(f"  {'TOTAL':<30} {fmt(headline_total)}")

    # ── Path B: Historical (as_of_date=AS_OF) ──────────────────────
    print(f"\n📈 PATH B — TREND (as_of_date={AS_OF}, historical replay)")
    print("-" * 100)

    # B.1: Market-replay aggregate (equity, MF, ESOP, gold, SGB)
    market_replay_total = historical_market_assets_value(
        session, user_id=USER_ID, as_of=AS_OF
    )
    print(f"\n  Market replay aggregate: {fmt(market_replay_total)}")

    # Show position-level breakdown
    positions = market_position_quantities_as_of(
        session, user_id=USER_ID, as_of=AS_OF
    )
    market_by_class: dict[str, float] = defaultdict(float)
    print(f"\n  {'Asset Class':<22} {'Symbol':<20} {'Qty':>12} {'Price':>12} {'Value':>16}")
    print(f"  {'—' * 22} {'—' * 20} {'—' * 12} {'—' * 12} {'—' * 16}")
    for (asset_class, symbol), qty in sorted(positions.items()):
        px = _latest_price_on_or_before(session, symbol, AS_OF)
        val = qty * px if px else 0.0
        market_by_class[asset_class] += val
        px_str = f"{px:>12.2f}" if px else "     NO PRICE"
        print(f"  {asset_class:<22} {symbol:<20} {qty:>12.4f} {px_str} {fmt(val)}")

    # B.2: Non-market-replay holdings (PPF, NPS, manual, etc.)
    print(f"\n  Non-market-replay holdings:")
    non_replay_by_class: dict[str, float] = defaultdict(float)
    non_replay_details: list[tuple[str, str, str, float]] = []

    for h in holdings:
        if is_market_replay_holding(h):
            continue
        val = _holding_value(session, h, AS_OF)
        non_replay_by_class[h.asset_class] += val
        non_replay_details.append((h.asset_class, h.name, h.symbol or "—", val))

    print(f"  {'Asset Class':<22} {'Name':<30} {'Symbol':<15} {'Value':>16}")
    print(f"  {'—' * 22} {'—' * 30} {'—' * 15} {'—' * 16}")
    for ac, name, sym, val in sorted(non_replay_details, key=lambda x: -x[3]):
        print(f"  {ac:<22} {name[:30]:<30} {sym:<15} {fmt(val)}")
    non_replay_total = sum(non_replay_by_class.values())
    print(f"  {'SUBTOTAL':<22} {'':<30} {'':<15} {fmt(non_replay_total)}")

    trend_total = market_replay_total + non_replay_total

    # ── B (cross-check): compute_net_worth with as_of_date ─────────
    nw_with_date = compute_net_worth(session, as_of_date=AS_OF, user_id=USER_ID)
    nw_without_date = compute_net_worth(session, as_of_date=None, user_id=USER_ID)

    # ── Summary comparison ─────────────────────────────────────────
    print("\n" + "=" * 100)
    print("🔍 COMPARISON")
    print("=" * 100)
    print(f"  Headline total (as_of=None):        {fmt(headline_total)}")
    print(f"  Trend total (as_of={AS_OF}):   {fmt(trend_total)}")
    print(f"    ↳ market replay portion:          {fmt(market_replay_total)}")
    print(f"    ↳ non-replay portion:             {fmt(non_replay_total)}")
    print(f"  compute_net_worth(None).assets:      {fmt(float(nw_without_date['total_assets']))}")
    print(f"  compute_net_worth({AS_OF}).assets: {fmt(float(nw_with_date['total_assets']))}")
    print(f"\n  DISCREPANCY: {fmt(headline_total - trend_total)}")

    # ── Per-asset-class diff ───────────────────────────────────────
    # Combine market replay + non-replay by class for path B
    trend_by_class: dict[str, float] = defaultdict(float)
    for k, v in market_by_class.items():
        trend_by_class[k] += v
    for k, v in non_replay_by_class.items():
        trend_by_class[k] += v

    all_classes = sorted(set(headline_by_class) | set(trend_by_class))
    print(f"\n  {'Asset Class':<25} {'Headline':>16} {'Trend':>16} {'Diff':>16}")
    print(f"  {'—' * 25} {'—' * 16} {'—' * 16} {'—' * 16}")
    for ac in all_classes:
        a = headline_by_class.get(ac, 0.0)
        b = trend_by_class.get(ac, 0.0)
        diff = a - b
        marker = " ⚠️" if abs(diff) > 1.0 else ""
        print(f"  {ac:<25} {fmt(a)} {fmt(b)} {fmt(diff)}{marker}")

    # ── Per-holding diff for market-replay holdings ────────────────
    print(f"\n  Per-holding diff (market replay holdings only):")
    print(f"  {'Name':<30} {'Headline':>14} {'Hist Replay':>14} {'Diff':>14}")
    print(f"  {'—' * 30} {'—' * 14} {'—' * 14} {'—' * 14}")
    for h in sorted(holdings, key=lambda x: x.asset_class):
        if not is_market_replay_holding(h):
            continue
        a_val = _holding_value(session, h, None)
        b_val = historical_market_holding_value(session, h, as_of=AS_OF)
        diff = a_val - b_val
        marker = " ⚠️" if abs(diff) > 1.0 else ""
        print(
            f"  {h.name[:30]:<30} {a_val:>14,.2f} {b_val:>14,.2f} {diff:>14,.2f}{marker}"
        )


if __name__ == "__main__":
    init_db()
    engine = get_engine()
    with Session(engine) as session:
        run_diagnosis(session)
