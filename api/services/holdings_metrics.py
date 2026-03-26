"""
Aggregates and per-holding metrics for the holdings API (Phase B3).

Cost basis is whatever we can infer from the Holding row (quantity × average cost,
or principal for fixed-income style rows). When that is missing, overall gain
fields stay None — the UI can still show current value and XIRR from txns.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any

from sqlmodel import Session, col, select

from api.models import Holding
from api.services.net_worth import holding_value
from api.services.returns_calculator import compute_returns

# Batch XIRR: cache keyed by (user_id, fingerprint). Invalidates when any active
# holding's updated_at changes (e.g. import, price refresh updating the row).
_batch_returns_cache: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
_BATCH_CACHE_MAX = 32


def clear_batch_returns_cache() -> None:
    """Test hook — drop cached batch return payloads."""
    _batch_returns_cache.clear()


def _utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.UTC).date()


def holding_cost_basis(h: Holding) -> float | None:
    """Deployed capital from the holding row, or None if we cannot infer it."""
    if h.quantity is not None and h.average_cost_per_unit is not None:
        q, ac = float(h.quantity), float(h.average_cost_per_unit)
        if q >= 0 and ac >= 0:
            return q * ac
    if h.principal_amount is not None and float(h.principal_amount) > 0:
        return float(h.principal_amount)
    return None


def active_holdings_for_user(session: Session, user_id: str) -> list[Holding]:
    q = (
        select(Holding)
        .where(
            Holding.user_id == user_id,
            Holding.is_active == True,  # noqa: E712
        )
        .order_by(col(Holding.name))
    )
    return list(session.exec(q).all())


def total_portfolio_value(session: Session, user_id: str) -> float:
    return sum(holding_value(session, h, None) for h in active_holdings_for_user(session, user_id))


def _holdings_change_fingerprint(session: Session, user_id: str) -> str:
    """Max updated_at among active holdings — shifts when any row changes."""
    q = (
        select(col(Holding.updated_at))
        .where(
            Holding.user_id == user_id,
            Holding.is_active == True,  # noqa: E712
        )
        .order_by(col(Holding.updated_at).desc())
        .limit(1)
    )
    row = session.exec(q).first()
    return row.isoformat() if row is not None else "none"


def compute_batch_returns(session: Session, user_id: str) -> dict[int, dict[str, Any]]:
    """XIRR / absolute return dict per holding id, with small in-process cache."""
    fp = _holdings_change_fingerprint(session, user_id)
    key = (user_id, fp)
    if key in _batch_returns_cache:
        return _batch_returns_cache[key]

    holdings = active_holdings_for_user(session, user_id)
    out: dict[int, dict[str, Any]] = {}
    for h in holdings:
        if h.id is None:
            continue
        out[h.id] = compute_returns(h.id, session)

    if len(_batch_returns_cache) >= _BATCH_CACHE_MAX:
        _batch_returns_cache.clear()
    _batch_returns_cache[key] = out
    return out


def overall_gain_for_holding(session: Session, h: Holding) -> tuple[float | None, float | None]:
    """(overall_gain, overall_gain_pct) using cost basis vs economic current value."""
    cv = holding_value(session, h, None)
    cb = holding_cost_basis(h)
    if cb is None:
        return None, None
    gain = cv - cb
    pct = (100.0 * gain / cb) if cb > 0 else None
    return round(gain, 2), round(pct, 2) if pct is not None else None


def asset_class_breakdown_and_totals(
    session: Session,
    user_id: str,
) -> tuple[
    float,
    float,
    float | None,
    float | None,
    dict[str, dict[str, float | None]],
]:
    """
    Returns:
      total_portfolio_value, total_cost_basis (sum of known costs only),
      total_overall_gain (sum of per-holding gains where cost known),
      total_overall_gain_pct (vs total_cost_basis if > 0),
      by_asset_class metrics for summary table.
    """
    holdings = active_holdings_for_user(session, user_id)
    total_cv = 0.0
    total_cb_known = 0.0
    total_gain_sum = 0.0
    gain_components = 0  # how many holdings contributed to total_gain_sum

    # Per class: investment (sum cost), current_value, gain sum where cost known
    inv_by: dict[str, float] = defaultdict(float)
    cv_by: dict[str, float] = defaultdict(float)
    gain_by: dict[str, float] = defaultdict(float)
    gain_count_by: dict[str, int] = defaultdict(int)

    for h in holdings:
        ac = h.asset_class
        cv = holding_value(session, h, None)
        total_cv += cv
        cv_by[ac] += cv
        cb = holding_cost_basis(h)
        if cb is not None:
            total_cb_known += cb
            inv_by[ac] += cb
            g = cv - cb
            total_gain_sum += g
            gain_components += 1
            gain_by[ac] += g
            gain_count_by[ac] += 1

    total_og: float | None = round(total_gain_sum, 2) if gain_components else None
    total_og_pct: float | None = None
    if total_cb_known > 0 and total_og is not None:
        total_og_pct = round(100.0 * total_gain_sum / total_cb_known, 2)

    by_class: dict[str, dict[str, float | None]] = {}
    for ac in sorted(set(cv_by.keys()) | set(inv_by.keys())):
        inv = inv_by.get(ac, 0.0)
        cv_c = cv_by.get(ac, 0.0)
        gb = gain_by.get(ac, 0.0) if gain_count_by.get(ac, 0) else None
        og_pct: float | None = None
        if inv > 0 and gb is not None:
            og_pct = round(100.0 * gb / inv, 2)
        by_class[ac] = {
            "investment": round(inv, 2),
            "current_value": round(cv_c, 2),
            "overall_gain": round(gb, 2) if gb is not None else None,
            "overall_gain_pct": og_pct,
        }

    return (
        round(total_cv, 2),
        round(total_cb_known, 2),
        total_og,
        total_og_pct,
        by_class,
    )


def portfolio_trend_start_date(end: datetime.date, range_key: str) -> datetime.date:
    """First calendar day of the window (approximate months for 3M/6M/12M)."""
    if range_key == "3M":
        return end - datetime.timedelta(days=92)
    if range_key == "6M":
        return end - datetime.timedelta(days=183)
    if range_key == "12M":
        return end - datetime.timedelta(days=365)
    if range_key == "all":
        # Long horizon for personal portfolio; history uses monthly anchors.
        return end - datetime.timedelta(days=365 * 20)
    raise ValueError(f"Unknown range: {range_key!r}")


def earliest_user_holding_date(session: Session, user_id: str) -> datetime.date | None:
    """Date of the oldest holding row for this user (for 'all' range start)."""
    q = (
        select(Holding)
        .where(Holding.user_id == user_id)
        .order_by(col(Holding.created_at).asc())
        .limit(1)
    )
    h = session.exec(q).first()
    if h is None:
        return None
    ca = h.created_at
    if isinstance(ca, datetime.datetime):
        return ca.date()
    return ca if isinstance(ca, datetime.date) else None
