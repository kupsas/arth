"""FIFO + India >12m calendar rule for equity LT/ST market-value split."""

from __future__ import annotations

import datetime

import pytest

from api.models import InvestmentTransaction
from api.services.equity_holding_period import (
    acquisition_is_long_term_india_equity,
    add_calendar_months,
    compute_equity_holding_period_split,
)


def test_add_calendar_months_clamps_day() -> None:
    assert add_calendar_months(datetime.date(2024, 1, 31), 1) == datetime.date(2024, 2, 29)
    assert add_calendar_months(datetime.date(2023, 1, 31), 12) == datetime.date(2024, 1, 31)


def test_long_term_strictly_after_twelve_month_anniversary() -> None:
    buy = datetime.date(2024, 1, 15)
    ann = add_calendar_months(buy, 12)
    assert ann == datetime.date(2025, 1, 15)
    assert not acquisition_is_long_term_india_equity(buy, ann)
    assert acquisition_is_long_term_india_equity(buy, ann + datetime.timedelta(days=1))


def _txn(
    *,
    tid: int,
    d: datetime.date,
    tt: str,
    qty: float,
    hid: int = 1,
) -> InvestmentTransaction:
    return InvestmentTransaction(
        id=tid,
        txn_date=d,
        symbol="RELIANCE",
        txn_type=tt,
        quantity=qty,
        price_per_unit=100.0,
        total_amount=qty * 100.0,
        account_platform="ICICI Direct",
        holding_id=hid,
    )


def test_fifo_split_two_lots_mixed_lt_st() -> None:
    as_of = datetime.date(2025, 6, 15)
    old = datetime.date(2023, 1, 1)
    recent = datetime.date(2025, 3, 1)
    txns = [
        _txn(tid=1, d=old, tt="BUY", qty=10.0),
        _txn(tid=2, d=recent, tt="BUY", qty=10.0),
    ]
    cmp_inr = 500.0
    cv = 20.0 * cmp_inr
    sp = compute_equity_holding_period_split(
        txns,
        holding_quantity=20.0,
        cmp_inr=cmp_inr,
        current_value_inr=cv,
        as_of=as_of,
    )
    assert sp.long_term_value_inr == pytest.approx(10.0 * cmp_inr)
    assert sp.short_term_value_inr == pytest.approx(10.0 * cmp_inr)
    assert sp.unallocated_value_inr == pytest.approx(0.0)
    assert sp.basis_note == "fifo_12m_india_listed_equity_cmp"


def test_fifo_sell_consumes_oldest_first() -> None:
    as_of = datetime.date(2025, 6, 15)
    txns = [
        _txn(tid=1, d=datetime.date(2023, 1, 1), tt="BUY", qty=10.0),
        _txn(tid=2, d=datetime.date(2025, 3, 1), tt="BUY", qty=10.0),
        _txn(tid=3, d=datetime.date(2025, 4, 1), tt="SELL", qty=8.0),
    ]
    cmp_inr = 100.0
    qty_h = 12.0
    cv = qty_h * cmp_inr
    sp = compute_equity_holding_period_split(
        txns,
        holding_quantity=qty_h,
        cmp_inr=cmp_inr,
        current_value_inr=cv,
        as_of=as_of,
    )
    # 2 shares left from 2023 lot (LT), 10 from 2025 lot (ST)
    assert sp.long_term_value_inr == pytest.approx(200.0)
    assert sp.short_term_value_inr == pytest.approx(1000.0)


def test_no_ledger_full_unallocated() -> None:
    sp = compute_equity_holding_period_split(
        [],
        holding_quantity=5.0,
        cmp_inr=200.0,
        current_value_inr=1000.0,
        as_of=datetime.date(2025, 1, 1),
    )
    assert sp.unallocated_value_inr == 1000.0
    assert sp.long_term_value_inr == 0.0
    assert sp.short_term_value_inr == 0.0
    assert "no_ledger" in sp.basis_note
