"""
Holding-level return metrics (Phase A.1.1).

We support three main ideas your spreadsheet might call "return":

1. **XIRR** — money-weighted annual return from dated cash flows (buys, sells,
   dividends) plus a final "you could sell today for this much" terminal value.
   Uses ``pyxirr`` (same family of math as Excel XIRR).

2. **Fixed compounding** — for PPF / FD-style rows where the bank quotes a rate
   and compounding frequency. We can show *stated* annual rate and/or *implied*
   CAGR from principal → current value over elapsed time.

3. **YTM** — yield-to-maturity style solve for simple bond/SGB math: coupons +
   redemption vs today's dirty price (we treat ``current_value`` as that price).

``compute_returns`` picks a path from ``valuation_method`` + ``asset_class`` so
callers (API routes later) do not need a big if/else tree.
"""

from __future__ import annotations

import datetime
import logging
import math
from typing import Any

import pyxirr
from scipy.optimize import newton
from sqlmodel import Session, col, select

from api.models import Holding, InvestmentTransaction
from pipeline.models import AssetClass, CompoundingFrequency, InvestmentTxnType, ValuationMethod

logger = logging.getLogger(__name__)

# Cash-flow sign convention for XIRR (investor = you):
#   Negative  → cash leaves your pocket (buys, SIPs).
#   Positive  → cash comes back (sells, dividends, terminal liquidation value).


def _utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.UTC).date()


def _interest_rate_decimal(rate: float | None) -> float:
    """Normalise stored rate: 7.1 means 7.1% → 0.071; 0.071 stays 0.071."""
    if rate is None:
        return 0.0
    return rate / 100.0 if rate > 1.0 else rate


def _compounding_periods_per_year(freq: str | None) -> int:
    if not freq:
        return 12
    mapping = {
        CompoundingFrequency.MONTHLY.value: 12,
        CompoundingFrequency.QUARTERLY.value: 4,
        CompoundingFrequency.HALF_YEARLY.value: 2,
        CompoundingFrequency.ANNUALLY.value: 1,
    }
    return mapping.get(freq, 12)


def _terminal_market_value(holding: Holding, as_of: datetime.date) -> float:
    """Best-effort mark for 'what the position is worth on as_of' for XIRR."""
    if holding.current_value is not None and holding.current_value > 0:
        return float(holding.current_value)
    if holding.quantity and holding.current_price_per_unit:
        return float(holding.quantity) * float(holding.current_price_per_unit)
    return 0.0


def _investment_txns_for_holding(
    session: Session,
    holding_id: int,
) -> list[InvestmentTransaction]:
    q = (
        select(InvestmentTransaction)
        .where(InvestmentTransaction.holding_id == holding_id)
        .order_by(col(InvestmentTransaction.txn_date), col(InvestmentTransaction.id))
    )
    return list(session.exec(q).all())


def _cashflow_amount_for_xirr(txn: InvestmentTransaction) -> float | None:
    """Map one ledger row to a signed cash amount for XIRR, or None to skip."""
    t = txn.txn_type
    amt = abs(float(txn.total_amount))

    # Money you pay to acquire / add units → negative.
    if t in (
        InvestmentTxnType.BUY.value,
        InvestmentTxnType.SIP.value,
        InvestmentTxnType.SWITCH_IN.value,
    ):
        return -amt

    # Money you receive (sale proceeds, dividends, switch-out proceeds) → positive.
    if t in (
        InvestmentTxnType.SELL.value,
        InvestmentTxnType.SWITCH_OUT.value,
        InvestmentTxnType.DIVIDEND.value,
    ):
        return amt

    logger.debug("Skipping txn id=%s type=%s for XIRR", txn.id, t)
    return None


def compute_xirr(
    holding_id: int,
    session: Session,
    *,
    as_of_date: datetime.date | None = None,
) -> float | None:
    """Annualised money-weighted return (decimal e.g. 0.12 = 12%) or None."""
    holding = session.get(Holding, holding_id)
    if not holding:
        return None

    as_of = as_of_date or _utc_today()
    txns = _investment_txns_for_holding(session, holding_id)
    dates: list[datetime.date] = []
    amounts: list[float] = []

    for txn in txns:
        if txn.txn_date > as_of:
            continue
        cf = _cashflow_amount_for_xirr(txn)
        if cf is None:
            continue
        dates.append(txn.txn_date)
        amounts.append(cf)

    terminal = _terminal_market_value(holding, as_of)
    if terminal > 0:
        dates.append(as_of)
        amounts.append(terminal)

    if len(dates) < 2:
        return None

    try:
        # pyxirr returns annual IRR as a float; silent=True avoids stderr noise on bad series.
        result = pyxirr.xirr(dates, amounts, silent=True)
        if result is None or (isinstance(result, float) and (math.isnan(result) or math.isinf(result))):
            return None
        return float(result)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("XIRR failed for holding_id=%s: %s", holding_id, exc)
        return None


def compute_fixed_return(
    holding: Holding,
    *,
    as_of_date: datetime.date | None = None,
) -> dict[str, Any]:
    """PPF/FD-style metrics from principal, quoted rate, and compounding."""
    as_of = as_of_date or _utc_today()
    principal = float(holding.principal_amount or 0.0)
    r = _interest_rate_decimal(holding.interest_rate)
    m = _compounding_periods_per_year(holding.compounding_frequency)

    # Elapsed years from created_at (import time) — good enough until we store
    # explicit "account open" dates on every instrument.
    start = holding.created_at.date() if holding.created_at else as_of
    if start >= as_of:
        years = 0.0
    else:
        years = (as_of - start).days / 365.25

    stated_annual = r
    # Future value if principal grew at stated rate with compound frequency m.
    fv_model = (
        principal * (1.0 + r / m) ** (m * years) if principal > 0 and r >= 0 else None
    )

    current = float(holding.current_value or 0.0)
    implied_cagr: float | None = None
    if principal > 0 and current > 0 and years > 0:
        implied_cagr = (current / principal) ** (1.0 / years) - 1.0

    return {
        "method": "fixed_return",
        "stated_annual_rate": stated_annual,
        "principal": principal,
        "years_elapsed": round(years, 4),
        "model_fv_if_rates_hold": fv_model,
        "current_value": current,
        "implied_cagr": implied_cagr,
        "absolute_return": current - principal,
    }


def _coupon_payments_per_year(coupon_frequency: str | None) -> int:
    if not coupon_frequency:
        return 2
    cf = coupon_frequency.upper()
    if "MONTH" in cf:
        return 12
    if "QUART" in cf:
        return 4
    if "SEMI" in cf or "HALF" in cf:
        return 2
    if "ANNUAL" in cf or "YEAR" in cf:
        return 1
    return 2


def compute_ytm(holding: Holding, *, as_of_date: datetime.date | None = None) -> float | None:
    """Solve yield-to-maturity (decimal) for a coupon bond; None if not solvable."""
    as_of = as_of_date or _utc_today()
    market = float(holding.current_value or 0.0)
    face = float(holding.face_value or 0.0)
    if market <= 0 or face <= 0 or not holding.maturity_date:
        return None

    coupon_rate = _interest_rate_decimal(holding.coupon_rate)
    n_per_year = _coupon_payments_per_year(holding.coupon_frequency)
    coupon_cash = face * coupon_rate / n_per_year

    mat = holding.maturity_date
    if mat <= as_of:
        return None

    # Whole coupon periods remaining (simple staircase; good enough for MVP).
    years_to_mat = (mat - as_of).days / 365.25
    n_periods = max(1, int(math.ceil(years_to_mat * n_per_year)))

    def dirty_price_from_yield(y: float) -> float:
        """PV of coupons + redemption at yield y (compounded n_per_year times per year)."""
        if y <= -0.9999 / n_per_year:
            return float("inf")
        pv = 0.0
        for k in range(1, n_periods + 1):
            pv += coupon_cash / (1.0 + y / n_per_year) ** k
        pv += face / (1.0 + y / n_per_year) ** n_periods
        return pv

    def f(y: float) -> float:
        return dirty_price_from_yield(y) - market

    try:
        ytm = newton(f, x0=0.07, tol=1e-6, maxiter=80)
        if math.isnan(ytm) or math.isinf(ytm):
            return None
        return float(ytm)
    except Exception:
        return None


def _cost_basis_from_txns(txns: list[InvestmentTransaction]) -> float:
    """Simple deployed capital: buys − sells − dividends (rough P&L helper)."""
    buys = 0.0
    sells_divs = 0.0
    for txn in txns:
        amt = abs(float(txn.total_amount))
        t = txn.txn_type
        if t in (
            InvestmentTxnType.BUY.value,
            InvestmentTxnType.SIP.value,
            InvestmentTxnType.SWITCH_IN.value,
        ):
            buys += amt
        elif t in (
            InvestmentTxnType.SELL.value,
            InvestmentTxnType.SWITCH_OUT.value,
            InvestmentTxnType.DIVIDEND.value,
        ):
            sells_divs += amt
    return buys - sells_divs


def compute_returns(
    holding_id: int,
    session: Session,
    *,
    as_of_date: datetime.date | None = None,
) -> dict[str, Any]:
    """Dispatcher: one dict for dashboards / APIs."""
    holding = session.get(Holding, holding_id)
    if not holding:
        return {"method": "unavailable", "error": "holding_not_found"}

    as_of = as_of_date or _utc_today()
    txns = _investment_txns_for_holding(session, holding_id)
    terminal = _terminal_market_value(holding, as_of)
    basis = _cost_basis_from_txns(txns)
    absolute = terminal - basis if txns else None

    vm = holding.valuation_method
    ac = holding.asset_class

    # Manual marks (halted stocks, private assets): no automated IRR.
    if vm == ValuationMethod.MANUAL.value:
        return {
            "method": "manual",
            "annualized_return": None,
            "absolute_return": absolute,
            "message": "MANUAL valuation — set returns in notes or spreadsheets.",
        }

    # Fixed-income style balance sheet items.
    if vm == ValuationMethod.FIXED_RETURN.value or ac in (
        AssetClass.PPF.value,
        AssetClass.FD.value,
    ):
        fixed = compute_fixed_return(holding, as_of_date=as_of)
        ann = fixed.get("implied_cagr")
        if ann is None:
            ann = fixed.get("stated_annual_rate")
        return {
            "method": "fixed_return",
            "annualized_return": ann,
            "absolute_return": fixed.get("absolute_return"),
            "detail": fixed,
        }

    # Sovereign gold bonds quote like bonds when we have coupon + redemption fields.
    if (
        ac == AssetClass.SOVEREIGN_GOLD_BOND.value
        and holding.face_value
        and holding.coupon_rate
        and holding.maturity_date
    ):
        ytm = compute_ytm(holding, as_of_date=as_of)
        return {
            "method": "ytm",
            "annualized_return": ytm,
            "absolute_return": absolute,
            "detail": {"ytm": ytm},
        }

    # Default for market-traded sleeves: XIRR when we have flows + terminal value.
    if vm == ValuationMethod.MARKET_PRICE.value:
        x = compute_xirr(holding_id, session, as_of_date=as_of)
        return {
            "method": "xirr",
            "annualized_return": x,
            "absolute_return": absolute,
            "detail": {"terminal_value_used": terminal, "txn_count": len(txns)},
        }

    return {
        "method": "unavailable",
        "annualized_return": None,
        "absolute_return": absolute,
        "message": "No return path matched valuation_method/asset_class.",
    }
