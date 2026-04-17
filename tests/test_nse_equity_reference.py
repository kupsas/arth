"""Tests for :mod:`api.services.nse_equity_reference` (index + bhav snapshot, no live NSE)."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import api.models  # noqa: F401 — register all ORM tables for create_all

from api.models import NseEquityReference
from api.services.nse_equity_reference import (
    instrument_kind_from_bhav_row,
    refresh_nse_equity_reference,
)


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


def test_instrument_kind_from_bhav_row_series() -> None:
    """Spot-check ``SCTYSRS`` → ``instrument_kind`` mapping (NSE uses STK for many types)."""
    stk = {"FININSTRMTP": "STK"}
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "RR"}) == "REIT"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "IV"}) == "INVIT"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "GB"}) == "SGB"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "GS"}) == "GSEC"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "TB"}) == "TBILL"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "SG"}) == "SDL"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "N1"}) == "NCD"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "NA"}) == "NCD"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "Z8"}) == "NCD"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "Y7"}) == "DEBT_STRUCTURED"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "E1"}) == "DEBT_STRUCTURED"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "EQ"}) == "EQUITY"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "P1"}) == "EQUITY"
    assert instrument_kind_from_bhav_row({**stk, "SCTYSRS": "XX"}) == "UNKNOWN"


def test_refresh_partitions_large_mid_small_and_kinds(monkeypatch: pytest.MonkeyPatch, engine) -> None:
    from api.services import nse_equity_reference as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)

    def fake_resolve(preferred: datetime.date):
        return datetime.date(2025, 1, 15), {"DUMMY": 1.0}

    monkeypatch.setattr(mod, "resolve_nse_bhav_session_and_map", fake_resolve)
    monkeypatch.setattr(
        mod,
        "load_nse_equity_bhav_full_rows",
        lambda _d: {
            "RELIANCE": {"CLSPRIC": "2500", "FININSTRMTP": "STK", "SCTYSRS": "EQ"},
            "TINYCAP": {"CLSPRIC": "1", "FININSTRMTP": "STK", "SCTYSRS": "EQ"},
            # Debt / hybrid rows — must appear in the table with the right ``instrument_kind``.
            "BADNCD": {"CLSPRIC": "99", "FININSTRMTP": "STK", "SCTYSRS": "N1"},
            "EMBASSY": {"CLSPRIC": "300", "FININSTRMTP": "STK", "SCTYSRS": "RR"},
            "SGB29": {"CLSPRIC": "5000", "FININSTRMTP": "STK", "SCTYSRS": "GB"},
        },
    )

    class FakeNse:
        def listEquityStocksByIndex(self, name: str) -> dict:
            if name == "NIFTY 100":
                return {
                    "data": [
                        {
                            "symbol": "RELIANCE",
                            "lastPrice": 2500,
                            "ffmc": 1e12,
                            "meta": {
                                "symbol": "RELIANCE",
                                "companyName": "Reliance Industries Limited",
                                "industry": "Oil",
                                "isin": "INE002A01018",
                            },
                        }
                    ]
                }
            if name == "NIFTY MIDCAP 150":
                return {
                    "data": [
                        {
                            "symbol": "MIDCO",
                            "lastPrice": 100,
                            "meta": {
                                "symbol": "MIDCO",
                                "companyName": "Mid Company",
                                "industry": "Textiles",
                                "isin": "INE999A01012",
                            },
                        }
                    ]
                }
            return {"data": []}

    monkeypatch.setattr(mod, "get_nse_client", lambda: FakeNse())

    with Session(engine) as session:
        stats = refresh_nse_equity_reference(session, commit=True)

    assert stats["large_cap"] == 1
    assert stats["mid_cap"] == 1
    assert stats["small_cap"] == 1
    assert stats["symbols_total"] == 6
    assert stats["instrument_kind"] == {
        "EQUITY": 3,
        "NCD": 1,
        "REIT": 1,
        "SGB": 1,
    }

    with Session(engine) as session:
        r1 = session.get(NseEquityReference, "RELIANCE")
        r2 = session.get(NseEquityReference, "MIDCO")
        r3 = session.get(NseEquityReference, "TINYCAP")
        bad = session.get(NseEquityReference, "BADNCD")
        reit = session.get(NseEquityReference, "EMBASSY")
        sgb = session.get(NseEquityReference, "SGB29")
    assert r1 is not None and r1.market_cap_class == "LARGE_CAP" and r1.instrument_kind == "EQUITY"
    assert r2 is not None and r2.market_cap_class == "MID_CAP" and r2.instrument_kind == "EQUITY"
    assert r3 is not None and r3.market_cap_class == "SMALL_CAP" and r3.instrument_kind == "EQUITY"
    assert bad is not None and bad.market_cap_class is None and bad.instrument_kind == "NCD"
    assert reit is not None and reit.market_cap_class is None and reit.instrument_kind == "REIT"
    assert sgb is not None and sgb.market_cap_class is None and sgb.instrument_kind == "SGB"
