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


def test_quarterly_cadence_flags_fully_empty_calendar_quarter(session: Session):
    """
    For ``quarterly`` senders, a *calendar quarter* with no rows in the covered
    range is a gap (e.g. Q2 empty between activity in Q1 and Q3).
    """
    user = "u_q1"
    src = "q_stmt_src"
    _add_txn(session, i=1, user=user, source=src, d=dt.date(2021, 1, 10))
    _add_txn(session, i=2, user=user, source=src, d=dt.date(2021, 10, 5))
    session.commit()
    cfg = {
        "a@b.com": {
            "display_name": "Quarterly PDF",
            "source_type": "savings",
            "expected_cadence": "quarterly",
            "accounts": {"1": {"source_key": src, "account_id": "a1"}},
        }
    }
    out = detect_gaps(session, user, cfg)
    assert len(out) == 1
    # Apr–Sep 2021 (inclusive) spans Q2 and Q3 with no transactions; algorithm reports
    # those zero months. At minimum Q2 2021 should appear.
    labels = [g["period_start"] for g in out[0]["gaps"]]
    assert "2021-04" in labels


def test_filter_onboarding_alert_ids_without_statement_phase_keeps_all(session: Session):
    from scraper.gap_detector import filter_onboarding_alert_ids_after_statements

    items = [
        {"id": "x1", "received_at": "2021-06-01T00:00:00+00:00"},
        {"id": "x2", "received_at": "2021-05-01T00:00:00+00:00"},
    ]
    cfg: dict = {}
    got = filter_onboarding_alert_ids_after_statements(
        session, "u_gap_f", "any", cfg, items, had_statement_ids_at_init=False
    )
    assert got == ["x2", "x1"]


def test_build_source_key_meta_prefers_monthly_cadence() -> None:
    from scraper.gap_detector import _build_source_key_meta

    cfg = {
        "alerts@bank.com": {
            "display_name": "Alerts",
            "source_type": "savings",
            "expected_cadence": "per_transaction",
            "accounts": {"1": {"source_key": "shared", "account_id": "A"}},
        },
        "stmt@bank.com": {
            "display_name": "Stmt",
            "source_type": "savings",
            "expected_cadence": "monthly",
            "accounts": {"1": {"source_key": "shared", "account_id": "A"}},
        },
    }
    meta = _build_source_key_meta(cfg)
    assert meta["shared"]["expected_cadence"] == "monthly"


def test_compute_alert_backfill_windows_includes_gap_and_pre_statement(session: Session):
    from scraper.gap_detector import compute_alert_backfill_windows

    user = "u_win"
    src = "hdfc_sav_win"
    _add_txn(session, i=1, user=user, source=src, d=dt.date(2022, 1, 5))
    _add_txn(session, i=2, user=user, source=src, d=dt.date(2022, 3, 2))
    session.commit()

    cfg = {
        "stmt@bank.com": {
            "display_name": "Stmt",
            "source_type": "savings",
            "expected_cadence": "monthly",
            "accounts": {"1": {"source_key": src, "account_id": "a1"}},
        },
    }
    ga = dt.date(2020, 1, 1)
    gb = dt.date(2023, 1, 1)
    wins = compute_alert_backfill_windows(
        session,
        user,
        src,
        cfg,
        gmail_after_inclusive=ga,
        gmail_before_exclusive=gb,
        had_statement_ids_at_init=True,
    )
    kinds = [w["kind"] for w in wins]
    assert "gap" in kinds
    assert "pre_statement" in kinds
    assert wins[0]["kind"] == "gap"


def test_compute_alert_backfill_windows_uncertain_is_capped(session: Session):
    from scraper.gap_detector import ALERT_BACKFILL_MAX_UNCERTAIN_WINDOWS, compute_alert_backfill_windows

    user = "u_cap"
    src = "orphan_src"
    session.commit()
    cfg = {
        "stmt@bank.com": {
            "display_name": "Stmt",
            "source_type": "savings",
            "expected_cadence": "monthly",
            "accounts": {"1": {"source_key": src, "account_id": "a1"}},
        },
    }
    ga = dt.date(2000, 1, 1)
    gb = dt.date(2026, 1, 1)
    wins = compute_alert_backfill_windows(
        session,
        user,
        src,
        cfg,
        gmail_after_inclusive=ga,
        gmail_before_exclusive=gb,
        had_statement_ids_at_init=True,
    )
    uncertain = [w for w in wins if w["kind"] == "coverage_uncertain"]
    assert len(uncertain) <= ALERT_BACKFILL_MAX_UNCERTAIN_WINDOWS
