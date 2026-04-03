"""
Split equity market value into long-term vs short-term buckets for India-listed shares.

Tax context (portfolio *classification*, not tax filing):
  Listed equity is a long-term capital asset only if it is held for **more than**
  twelve months from the date of acquisition (Income Tax Act — commonly summarized
  as “> 12 months”; we implement that as “first long-term day is the calendar day
  after the 12‑month anniversary of the buy date”).

Why FIFO:
  On a sale, Indian practice for computing gain uses specific identification where
  documented; otherwise FIFO is a standard conservative default. For *unrealised*
  lots we apply the same FIFO queue so sells consume oldest purchases first.

A single script (one holding row) can therefore contribute to both LT and ST at
once: different purchase dates produce different lots, each marked at the same
CMP for the holding.
"""

from __future__ import annotations

import calendar
import datetime
from collections import defaultdict, deque
from dataclasses import dataclass

from sqlmodel import Session, col, select

from api.models import Holding, InvestmentTransaction
from pipeline.models import AssetClass, InvestmentTxnType, ValuationMethod

# Buys increase quantity; sells reduce oldest lots first (FIFO).
_LOT_IN_TYPES = frozenset(
    {
        InvestmentTxnType.BUY.value,
        InvestmentTxnType.SIP.value,
        InvestmentTxnType.SWITCH_IN.value,
    }
)
_LOT_OUT_TYPES = frozenset(
    {
        InvestmentTxnType.SELL.value,
        InvestmentTxnType.SWITCH_OUT.value,
    }
)


def add_calendar_months(d: datetime.date, months: int) -> datetime.date:
    """Add calendar months; clamp day to the last valid day in the target month."""
    total_month = d.month - 1 + months
    year = d.year + total_month // 12
    month = total_month % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(d.day, last_day)
    return datetime.date(year, month, day)


def acquisition_is_long_term_india_equity(
    buy_date: datetime.date,
    as_of: datetime.date,
) -> bool:
    """
    True when ``as_of`` is strictly after the 12-month anniversary of ``buy_date``.

    Example: buy 2024-01-15 → anniversary 2025-01-15 → LT from 2025-01-16 onward.
    """
    if as_of <= buy_date:
        return False
    twelfth_month_end = add_calendar_months(buy_date, 12)
    return as_of > twelfth_month_end


@dataclass(frozen=True)
class EquityHoldingPeriodSplit:
    """Rupee market-value split at the holding's CMP (same price for all lots)."""

    long_term_value_inr: float
    short_term_value_inr: float
    unallocated_value_inr: float
    fifo_quantity_after_txns: float
    basis_note: str


def _fifo_lots_after_txns(
    txns: list[InvestmentTransaction],
) -> list[tuple[float, datetime.date]]:
    """
    Return remaining (quantity, buy_date) lots after processing txns in order.

    DIVIDEND and unknown types do not change share quantity.
    """
    # deque of (qty, buy_date)
    lots: deque[tuple[float, datetime.date]] = deque()

    for t in txns:
        tt = t.txn_type
        if tt in _LOT_IN_TYPES:
            lots.append((float(t.quantity), t.txn_date))
        elif tt in _LOT_OUT_TYPES:
            sell_left = float(t.quantity)
            while sell_left > 1e-9 and lots:
                q0, d0 = lots[0]
                take = min(q0, sell_left)
                q0 -= take
                sell_left -= take
                if q0 <= 1e-9:
                    lots.popleft()
                else:
                    lots[0] = (q0, d0)
            # Ignore residual sell_left (data error / short) — unallocated will absorb.

    return [(q, d) for q, d in lots if q > 1e-9]


def _split_lot_values_at_cmp(
    lots: list[tuple[float, datetime.date]],
    *,
    cmp_inr: float,
    as_of: datetime.date,
    scale_to_holding_qty: float | None,
) -> tuple[float, float, float]:
    """
    Return (lt_value, st_value, unallocated_value).

    When ``scale_to_holding_qty`` is set and FIFO total qty > 0, scale each lot's
    quantity so summed lots match the holding row (handles minor ledger drift).
    """
    if cmp_inr <= 0 or not lots:
        return 0.0, 0.0, 0.0

    fifo_qty = sum(q for q, _ in lots)
    if fifo_qty <= 1e-9:
        return 0.0, 0.0, 0.0

    factor = 1.0
    if scale_to_holding_qty is not None and scale_to_holding_qty > 1e-9:
        factor = float(scale_to_holding_qty) / fifo_qty

    lt = 0.0
    st = 0.0
    for q, buy_date in lots:
        q_eff = q * factor
        v = round(q_eff * cmp_inr, 2)
        if v <= 0:
            continue
        if acquisition_is_long_term_india_equity(buy_date, as_of):
            lt += v
        else:
            st += v
    lt = round(lt, 2)
    st = round(st, 2)
    return lt, st, 0.0


def compute_equity_holding_period_split(
    txns: list[InvestmentTransaction],
    *,
    holding_quantity: float | None,
    cmp_inr: float | None,
    current_value_inr: float | None,
    as_of: datetime.date,
) -> EquityHoldingPeriodSplit:
    """
    Derive LT/ST/unallocated market values for one equity holding.

    * Unallocated * current market value when there is no usable ledger, CMP
      missing, or FIFO quantity is zero while the holding still shows stock.
    """
    note = "fifo_12m_india_listed_equity_cmp"
    cv = float(current_value_inr or 0.0)
    cmp_u = float(cmp_inr or 0.0)
    hq = float(holding_quantity) if holding_quantity is not None else 0.0

    if not txns or cmp_u <= 0 or cv <= 0:
        return EquityHoldingPeriodSplit(
            long_term_value_inr=0.0,
            short_term_value_inr=0.0,
            unallocated_value_inr=round(cv, 2),
            fifo_quantity_after_txns=0.0,
            basis_note="no_ledger_or_cmp_for_split",
        )

    ordered = sorted(txns, key=lambda t: (t.txn_date, t.id or 0))
    lots = _fifo_lots_after_txns(ordered)
    fifo_qty = sum(q for q, _ in lots)

    if fifo_qty <= 1e-9:
        return EquityHoldingPeriodSplit(
            long_term_value_inr=0.0,
            short_term_value_inr=0.0,
            unallocated_value_inr=round(cv, 2),
            fifo_quantity_after_txns=0.0,
            basis_note="fifo_qty_zero_unallocated_full_mark",
        )

    lt, st, _ = _split_lot_values_at_cmp(
        lots,
        cmp_inr=cmp_u,
        as_of=as_of,
        scale_to_holding_qty=hq if hq > 1e-9 else None,
    )
    allocated = round(lt + st, 2)
    # If scaled lots diverge from stored current_value, park the gap in unallocated.
    unalloc = max(0.0, round(cv - allocated, 2))
    return EquityHoldingPeriodSplit(
        long_term_value_inr=lt,
        short_term_value_inr=st,
        unallocated_value_inr=unalloc,
        fifo_quantity_after_txns=round(fifo_qty, 6),
        basis_note=note,
    )


def batch_equity_holding_period_splits(
    session: Session,
    holdings: list[Holding],
    *,
    as_of: datetime.date,
) -> dict[int, EquityHoldingPeriodSplit]:
    """
    Load investment_transactions once for all equity MARKET_PRICE holdings and
    return holding_id → split.
    """
    eq_ids = [
        h.id
        for h in holdings
        if h.id is not None
        and h.asset_class == AssetClass.EQUITY.value
        and h.valuation_method == ValuationMethod.MARKET_PRICE.value
    ]
    if not eq_ids:
        return {}

    rows = list(
        session.exec(
            select(InvestmentTransaction)
            .where(col(InvestmentTransaction.holding_id).in_(tuple(eq_ids)))
            .order_by(
                col(InvestmentTransaction.holding_id),
                col(InvestmentTransaction.txn_date),
                col(InvestmentTransaction.id),
            )
        ).all()
    )
    by_hid: dict[int, list[InvestmentTransaction]] = defaultdict(list)
    for r in rows:
        if r.holding_id is not None:
            by_hid[r.holding_id].append(r)

    out: dict[int, EquityHoldingPeriodSplit] = {}
    by_id = {h.id: h for h in holdings if h.id is not None}
    for hid in eq_ids:
        h = by_id.get(hid)
        if h is None:
            continue
        out[hid] = compute_equity_holding_period_split(
            by_hid.get(hid, []),
            holding_quantity=h.quantity,
            cmp_inr=h.current_price_per_unit,
            current_value_inr=h.current_value,
            as_of=as_of,
        )
    return out
