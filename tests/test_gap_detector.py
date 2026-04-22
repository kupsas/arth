"""Unit tests for ``scraper.gap_detector`` (Track 2 Phase 4a)."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.models import Transaction
from scraper.gap_detector import detect_gaps, _iter_months_inclusive


def _hasher(i: int) -> str:
    return f"hx{i:064x}"


def _add_txn(
    session: Session,
    *,
    i: int,
    user: str,
    source: str,
    d: dt.date,
) -> None:
    t = Transaction(
        content_hash=_hasher(i),
        txn_date=d,
        account_id="a1",
        user_id=user,
        source_statement=source,
        direction="OUTFLOW",
        amount=1.0,
        raw_description="x",
    )
    session.add(t)


@pytest.fixture(name="engine")
def _engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(name="session")
def _session(engine):
    with Session(engine) as s:
        yield s


def test_iter_months_inclusive():
    assert _iter_months_inclusive("2021-01", "2021-03") == [
        "2021-01",
        "2021-02",
        "2021-03",
    ]


def test_monthly_gap_between_two_active_months(session: Session):
    user = "u1"
    src = "hdfc_sav"
    _add_txn(session, i=1, user=user, source=src, d=dt.date(2021, 1, 5))
    _add_txn(session, i=2, user=user, source=src, d=dt.date(2021, 3, 2))
    session.commit()

    cfg = {
        "a@b.com": {
            "display_name": "Test",
            "source_type": "savings",
            "expected_cadence": "monthly",
            "accounts": {"1": {"source_key": src, "account_id": "a1"}},
        }
    }
    out = detect_gaps(session, user, cfg)
    assert len(out) == 1
    assert out[0]["gaps"] and out[0]["gaps"][0]["period_start"] == "2021-02"


def test_credit_card_ignores_two_quiet_months(session: Session):
    user = "u2"
    src = "hdfc_cc"
    for i, m in enumerate((1, 2, 5, 6), start=1):
        _add_txn(session, i=i, user=user, source=src, d=dt.date(2021, m, 1))
    session.commit()

    cfg = {
        "a@b.com": {
            "display_name": "CC",
            "source_type": "credit_card",
            "expected_cadence": "monthly",
            "accounts": {"1": {"source_key": src, "account_id": "a1"}},
        }
    }
    out = detect_gaps(session, user, cfg)
    assert out[0]["gaps"] == []


def test_credit_card_flags_three_consecutive_quiet_months(session: Session):
    user = "u3"
    src = "hdfc_cc2"
    _add_txn(session, i=1, user=user, source=src, d=dt.date(2021, 1, 1))
    _add_txn(session, i=2, user=user, source=src, d=dt.date(2021, 6, 1))
    session.commit()

    cfg = {
        "a@b.com": {
            "display_name": "CC",
            "source_type": "credit_card",
            "expected_cadence": "monthly",
            "accounts": {"1": {"source_key": src, "account_id": "a1"}},
        }
    }
    out = detect_gaps(session, user, cfg)
    assert len(out[0]["gaps"]) == 1
    g0 = out[0]["gaps"][0]
    # Jan and Jun have txns; Feb–May are four consecutive empty months.
    assert g0["period_start"] == "2021-02" and g0["period_end"] == "2021-05"


def test_per_transaction_no_month_gaps(session: Session):
    user = "u4"
    src = "nse"
    for i in range(3):
        _add_txn(
            session,
            i=i + 1,
            user=user,
            source=src,
            d=dt.date(2021, i * 2 + 1, 1),
        )
    session.commit()
    cfg = {
        "a@b.com": {
            "display_name": "Broker",
            "source_type": "broker",
            "expected_cadence": "per_transaction",
            "accounts": {"1": {"source_key": src, "account_id": "a1"}},
        }
    }
    out = detect_gaps(session, user, cfg)
    assert out[0]["gaps"] == []
    assert "Sporadic" in (out[0].get("note") or "")
