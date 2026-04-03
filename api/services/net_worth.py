"""
Net worth and portfolio structure helpers (Phase A.1.3).

**Net worth** here means: sum of *active* holding values minus *active* liability
principal (what you still owe).  Cash in bank accounts is *not* in the Holding
table yet unless you model it — so this is "Layer 1" investable / tracked assets.

When you pass ``as_of_date``, historical valuation is asset-class specific:

- market-priced sleeves replay quantities from linked ``investment_transactions``
  and mark them with the latest ``prices`` row on or before the anchor date
- PPF uses linked ledger rows (contribution / interest / withdrawal)
- NPS uses dated statement snapshots imported from CRA files
- unsupported sleeves still fall back to the stored holding row, gated by the
  holding's creation date so we do not show obvious pre-creation value
"""

from __future__ import annotations

import calendar
import datetime
from collections import defaultdict
from typing import Literal

from sqlalchemy import func
from sqlmodel import Session, col, select

from api.models import Holding, Liability, Price
from api.services.historical_portfolio import (
    historical_market_assets_value,
    historical_market_holding_value,
    historical_nps_holding_value,
    historical_ppf_holding_value,
    holding_created_on_or_before,
    is_excluded_historical_symbol,
    is_market_replay_holding,
    market_position_quantities_as_of,
)
from api.services.price_feed import canonical_nse_symbol
from pipeline.models import AssetClass, ValuationMethod

Granularity = Literal["daily", "weekly", "monthly"]


def _active_holdings(session: Session, user_id: str | None) -> list[Holding]:
    q = select(Holding).where(
        Holding.is_active == True,  # noqa: E712
    )
    if user_id:
        q = q.where(Holding.user_id == user_id)
    return list(session.exec(q).all())


def _active_liabilities(session: Session, user_id: str | None) -> list[Liability]:
    q = select(Liability).where(
        Liability.is_active == True,  # noqa: E712
    )
    if user_id:
        q = q.where(Liability.user_id == user_id)
    return list(session.exec(q).all())


def _latest_price_on_or_before(
    session: Session,
    symbol: str,
    as_of: datetime.date,
) -> float | None:
    q = (
        select(Price.close_price)
        .where(Price.symbol == symbol, Price.date <= as_of)
        .order_by(col(Price.date).desc())
        .limit(1)
    )
    row = session.exec(q).first()
    return float(row) if row is not None else None


def holding_value(
    session: Session,
    h: Holding,
    as_of_date: datetime.date | None = None,
) -> float:
    """Public: economic value of one holding (same rules as net worth snapshots)."""
    return _holding_value(session, h, as_of_date)


def _holding_value(
    session: Session,
    h: Holding,
    as_of: datetime.date | None,
) -> float:
    """Single holding economic value in INR (best effort)."""
    if as_of is None:
        return float(h.current_value or 0.0)

    if is_excluded_historical_symbol(h.symbol):
        return 0.0

    # PPF, NPS, and market-replay have transaction/snapshot-level dates that
    # naturally handle "does this holding exist as of X?" — no created_at gate.
    if h.asset_class == AssetClass.PPF.value:
        return historical_ppf_holding_value(session, h, as_of=as_of)

    if h.asset_class == AssetClass.NPS.value:
        return historical_nps_holding_value(session, h, as_of=as_of)

    if is_market_replay_holding(h):
        return historical_market_holding_value(session, h, as_of=as_of)

    # For remaining holdings (manual / fixed-return) we have no historical txn
    # data, so gate on created_at to avoid projecting current_value into the past.
    if not holding_created_on_or_before(h, as_of):
        return 0.0

    if h.valuation_method in (
        ValuationMethod.MANUAL.value,
        ValuationMethod.FIXED_RETURN.value,
    ):
        return float(h.current_value or 0.0)

    if h.valuation_method != ValuationMethod.MARKET_PRICE.value:
        return float(h.current_value or 0.0)

    if not h.symbol or h.quantity is None:
        return float(h.current_value or 0.0)

    sym = h.symbol.strip()
    # Mutual fund rows store AMFI numeric codes as symbol.
    if h.asset_class == AssetClass.MUTUAL_FUND.value and sym.isdigit():
        lookup = sym
    elif h.asset_class == AssetClass.GOLD.value:
        lookup = sym
    else:
        lookup = canonical_nse_symbol(sym)

    px = _latest_price_on_or_before(session, lookup, as_of)
    if px is None:
        return float(h.current_value or 0.0)
    return float(h.quantity) * px


def _historical_total_assets(
    session: Session,
    *,
    as_of_date: datetime.date,
    user_id: str | None,
) -> float:
    """Historical gross assets for the trend chart and net-worth history.

    Market-priced replayable sleeves are handled in one aggregate pass so sold
    historical positions still appear before exit, even if their holding row is
    inactive today.
    """
    uid = user_id.strip() if user_id and user_id.strip() else None
    total = 0.0
    if uid:
        total += historical_market_assets_value(session, user_id=uid, as_of=as_of_date)

    for h in _active_holdings(session, user_id):
        if is_market_replay_holding(h):
            continue
        total += _holding_value(session, h, as_of_date)
    return round(total, 2)


def historical_asset_class_values(
    session: Session,
    *,
    as_of_date: datetime.date,
    user_id: str | None,
) -> dict[str, float]:
    """Same valuation rules as ``_historical_total_assets``, split by ``asset_class``.

    Market replay positions are bucketed by the asset class on the position key;
    PPF / NPS / manual / etc. use each holding's ``asset_class``.
    """
    uid = user_id.strip() if user_id and user_id.strip() else None
    by_ac: dict[str, float] = defaultdict(float)
    if uid:
        positions = market_position_quantities_as_of(session, user_id=uid, as_of=as_of_date)
        for (asset_class, symbol), qty in positions.items():
            px = _latest_price_on_or_before(session, symbol, as_of_date)
            if px is None:
                continue
            by_ac[asset_class] += qty * px

    for h in _active_holdings(session, user_id):
        if is_market_replay_holding(h):
            continue
        v = _holding_value(session, h, as_of_date)
        by_ac[h.asset_class] += v

    return {k: round(v, 2) for k, v in by_ac.items() if v > 1e-9}


def portfolio_live_as_of_date(
    session: Session,
    *,
    user_id: str | None = None,
) -> datetime.date | None:
    """Latest ``last_valued_date`` among active market-priced holdings.

    After POST /prices/refresh, marks are written with the NAV/bhav row date — so
    this stays on e.g. 27 Mar until the next refresh moves it forward, even if the
    user opens the app days later without refreshing.
    """
    clauses: list = [
        Holding.is_active == True,  # noqa: E712
        col(Holding.last_valued_date).isnot(None),
        Holding.valuation_method == ValuationMethod.MARKET_PRICE.value,
    ]
    if user_id:
        clauses.append(Holding.user_id == user_id)
    q = select(func.max(Holding.last_valued_date)).where(*clauses)
    return session.exec(q).first()


def compute_net_worth(
    session: Session,
    *,
    as_of_date: datetime.date | None = None,
    user_id: str | None = None,
) -> dict[str, float | str | None]:
    """Total assets, liabilities, and net (assets − debt)."""
    liabilities = _active_liabilities(session, user_id)
    if as_of_date is None:
        holdings = _active_holdings(session, user_id)
        assets = sum(_holding_value(session, h, as_of_date) for h in holdings)
    else:
        assets = _historical_total_assets(session, as_of_date=as_of_date, user_id=user_id)
    debt = sum(float(x.principal_outstanding) for x in liabilities)
    net = assets - debt

    as_of_out: str | None
    if as_of_date is not None:
        as_of_out = as_of_date.isoformat()
    else:
        live = portfolio_live_as_of_date(session, user_id=user_id)
        as_of_out = live.isoformat() if live is not None else None

    return {
        "total_assets": round(assets, 2),
        "total_liabilities": round(debt, 2),
        "net_worth": round(net, 2),
        "as_of": as_of_out,
    }


def compute_asset_allocation(
    session: Session,
    *,
    as_of_date: datetime.date | None = None,
    user_id: str | None = None,
) -> dict[str, dict[str, float]]:
    """Three breakdowns as percentage of *gross* assets (0–100)."""
    holdings = _active_holdings(session, user_id)
    total = sum(_holding_value(session, h, as_of_date) for h in holdings)
    if total <= 0:
        return {
            "by_asset_class": {},
            "by_liquidity_class": {},
            "by_account_platform": {},
        }

    by_ac: dict[str, float] = defaultdict(float)
    by_liq: dict[str, float] = defaultdict(float)
    by_plat: dict[str, float] = defaultdict(float)

    for h in holdings:
        v = _holding_value(session, h, as_of_date)
        by_ac[h.asset_class] += v
        by_liq[h.liquidity_class] += v
        by_plat[h.account_platform] += v

    def pct(m: dict[str, float]) -> dict[str, float]:
        return {k: round(100.0 * v / total, 2) for k, v in sorted(m.items(), key=lambda x: -x[1])}

    return {
        "by_asset_class": pct(by_ac),
        "by_liquidity_class": pct(by_liq),
        "by_account_platform": pct(by_plat),
    }


def compute_concentration(
    session: Session,
    *,
    as_of_date: datetime.date | None = None,
    user_id: str | None = None,
) -> dict[str, float | str | None]:
    """Largest position weighting and ESOP sleeve size."""
    holdings = _active_holdings(session, user_id)
    values = [(h, _holding_value(session, h, as_of_date)) for h in holdings]
    total = sum(v for _, v in values)
    if total <= 0:
        return {
            "largest_holding_pct": None,
            "largest_holding_name": None,
            "esop_pct": None,
        }
    largest_h, largest_v = max(values, key=lambda x: x[1])
    esop_v = sum(v for h, v in values if h.asset_class == AssetClass.ESOP.value)
    return {
        "largest_holding_pct": round(100.0 * largest_v / total, 2),
        "largest_holding_name": largest_h.name,
        "esop_pct": round(100.0 * esop_v / total, 2) if esop_v else 0.0,
    }


def _last_day_of_month(year: int, month: int) -> datetime.date:
    """Calendar last day (handles leap years)."""
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, last)


def _iter_period_starts(start: datetime.date, end: datetime.date, g: Granularity) -> list[datetime.date]:
    """Generate anchor dates for history (inclusive of range endpoints)."""
    out: list[datetime.date] = []
    if start > end:
        return out
    if g == "daily":
        d = start
        while d <= end:
            out.append(d)
            d += datetime.timedelta(days=1)
        return out
    if g == "weekly":
        d = start
        while d <= end:
            out.append(d)
            d += datetime.timedelta(days=7)
        if out and out[-1] != end:
            out.append(end)
        return out
    # monthly — last calendar day of each month in range; for the month containing
    # `end`, use `end` (typically today) so the current month is "latest day so far".
    y, m = start.year, start.month
    end_ym = (end.year, end.month)
    while (y, m) <= end_ym:
        if (y, m) == end_ym:
            anchor = end
        else:
            anchor = _last_day_of_month(y, m)
        if anchor >= start and anchor <= end:
            out.append(anchor)
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    out.sort()
    return out


def net_worth_history_anchor_dates(
    start_date: datetime.date,
    end_date: datetime.date,
    granularity: Granularity = "monthly",
) -> list[datetime.date]:
    """Public wrapper for monthly/daily/weekly anchor dates used by history APIs."""
    return _iter_period_starts(start_date, end_date, granularity)


def compute_net_worth_history(
    session: Session,
    start_date: datetime.date,
    end_date: datetime.date,
    granularity: Granularity = "monthly",
    *,
    user_id: str | None = None,
) -> list[dict[str, float | str | None]]:
    """Time series of net worth (recomputed at each anchor date)."""
    points = _iter_period_starts(start_date, end_date, granularity)
    series: list[dict[str, float | str | None]] = []
    for d in points:
        snap = compute_net_worth(session, as_of_date=d, user_id=user_id)
        series.append(
            {
                "date": d.isoformat(),
                "net_worth": snap["net_worth"],
                "total_assets": snap["total_assets"],
                "total_liabilities": snap["total_liabilities"],
            }
        )
    return series


def liability_summary(session: Session, *, user_id: str | None = None) -> dict[str, float]:
    """Quick debt aggregates for dashboards."""
    rows = _active_liabilities(session, user_id)
    total_out = sum(float(r.principal_outstanding) for r in rows)
    total_emi = sum(float(r.emi_amount or 0.0) for r in rows)
    nw = compute_net_worth(session, user_id=user_id)
    ta = nw["total_assets"]
    assets = float(ta) if isinstance(ta, (int, float)) else 0.0
    ratio = (total_out / assets) if assets > 0 else 0.0
    return {
        "principal_outstanding": round(total_out, 2),
        "monthly_emi_burden": round(total_emi, 2),
        "debt_to_asset_ratio": round(ratio, 4),
    }
