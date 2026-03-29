"""
Phase A.6 — FastAPI routes for holdings, investment txns, liabilities, prices.

Reuses the in-memory DB + auth override pattern from ``test_db_and_api``.
"""

from __future__ import annotations

import datetime
import io
import os
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("ascii"))

from api.auth import get_current_user  # noqa: E402
from api.database import get_session  # noqa: E402
from api.main import app  # noqa: E402
from api.models import Holding, InvestmentTransaction, Price  # noqa: E402
from pipeline.models import (  # noqa: E402
    AssetClass,
    InvestmentTxnType,
    LiabilityType,
    LiquidityClass,
    ValuationMethod,
)


@pytest.fixture(name="engine")
def in_memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(name="client")
def api_client(engine, monkeypatch):
    # Lifespan still runs: avoid real DB + NSE in ``run_startup_price_sync`` and
    # avoid APScheduler threads (same pitfall as production ``get_engine()``).
    monkeypatch.setattr(
        "api.main.run_startup_price_sync",
        lambda _session: {"skipped": True, "reason": "test"},
    )
    monkeypatch.setattr("api.main.start_scheduler", lambda: None)
    monkeypatch.setattr("api.main.shutdown_scheduler", lambda: None)

    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "test_user"

    import api.database as _db_mod

    _original_init = _db_mod.init_db
    _db_mod.init_db = lambda: None

    with TestClient(app) as c:
        yield c

    _db_mod.init_db = _original_init
    app.dependency_overrides.clear()


def _seed_holding(session: Session) -> int:
    h = Holding(
        name="API Test Equity",
        symbol="TESTAPI",
        quantity=5.0,
        asset_class=AssetClass.EQUITY.value,
        account_platform="Unit",
        valuation_method=ValuationMethod.MANUAL.value,
        liquidity_class=LiquidityClass.T_PLUS_1.value,
        current_value=25_000.0,
        user_id="sashank",
    )
    session.add(h)
    session.commit()
    session.refresh(h)
    return h.id


@patch(
    "api.routes.holdings.get_ppf_reference_rate_for_projection",
    return_value=(7.1, "stub — not calling Wikipedia in test"),
)
def test_list_ppf_holding_includes_ppf_projection_fields(_mock_rate: MagicMock, client: TestClient, engine):
    with Session(engine) as s:
        h = Holding(
            name="Public Provident Fund (PPF)",
            asset_class=AssetClass.PPF.value,
            account_platform="ICICI PPF",
            valuation_method=ValuationMethod.FIXED_RETURN.value,
            liquidity_class=LiquidityClass.ILLIQUID.value,
            current_value=701_830.0,
            principal_amount=577_500.0,
            user_id="sashank",
        )
        s.add(h)
        s.commit()
        s.refresh(h)
        s.add(
            InvestmentTransaction(
                txn_date=datetime.date(2015, 4, 10),
                txn_type=InvestmentTxnType.BUY.value,
                quantity=1.0,
                price_per_unit=50_000.0,
                total_amount=50_000.0,
                account_platform="ICICI PPF",
                holding_id=h.id,
            )
        )
        s.commit()

    r = client.get("/api/holdings?user_id=sashank")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["asset_class"] == AssetClass.PPF.value
    assert row["ppf_first_contribution_date"] == "2015-04-10"
    assert row["ppf_projection_annual_rate_pct"] == pytest.approx(7.1)
    assert row["ppf_projected_value_at_maturity"] is not None
    assert row["ppf_projected_value_at_maturity"] > row["current_value"]
    assert row["maturity_date"] == "2031-03-31"


def test_list_nps_holding_includes_exit_projection_when_dob_in_env(
    client: TestClient, engine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DOB", "1990-06-15")
    monkeypatch.setenv("NPS_PROJECTION_ANNUAL_RATE_PCT", "10")
    with Session(engine) as s:
        h = Holding(
            name="National Pension System (NPS)",
            asset_class=AssetClass.NPS.value,
            account_platform="NPS (CRA)",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.ILLIQUID.value,
            current_value=100_000.0,
            user_id="sashank",
        )
        s.add(h)
        s.commit()

    r = client.get("/api/holdings?user_id=sashank")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["asset_class"] == AssetClass.NPS.value
    assert row["maturity_date"] == "2050-06-15"
    assert row["nps_projection_annual_rate_pct"] == pytest.approx(10.0)
    assert row["nps_projected_value_at_normal_exit"] is not None
    assert row["nps_projected_value_at_normal_exit"] > row["current_value"]
    assert row["nps_projection_note"]


def test_list_nps_holding_skips_projection_when_dob_missing(
    client: TestClient, engine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("DOB", raising=False)
    with Session(engine) as s:
        h = Holding(
            name="National Pension System (NPS)",
            asset_class=AssetClass.NPS.value,
            account_platform="NPS (CRA)",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.ILLIQUID.value,
            current_value=50_000.0,
            user_id="sashank",
        )
        s.add(h)
        s.commit()

    r = client.get("/api/holdings?user_id=sashank")
    assert r.status_code == 200
    row = r.json()[0]
    assert row["nps_projected_value_at_normal_exit"] is None
    assert row["nps_projection_annual_rate_pct"] is None


def test_list_and_get_holding(client: TestClient, engine):
    with Session(engine) as s:
        hid = _seed_holding(s)

    r = client.get("/api/holdings?user_id=sashank")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "API Test Equity"
    # B3 — computed fields present (no avg cost on seed row → gain unknown).
    assert rows[0]["weight_pct"] == pytest.approx(100.0)
    assert rows[0]["overall_gain"] is None
    assert rows[0]["overall_gain_pct"] is None

    r2 = client.get(f"/api/holdings/{hid}?user_id=sashank")
    assert r2.status_code == 200
    body = r2.json()
    assert body["holding"]["id"] == hid
    assert "returns" in body
    assert body["holding"]["weight_pct"] == pytest.approx(100.0)


def test_list_equity_market_price_includes_holding_period_split(
    client: TestClient, engine
):
    """GET /holdings attaches FIFO LT/ST CMP split for MARKET_PRICE equities."""
    with Session(engine) as s:
        h = Holding(
            name="Split Demo",
            symbol="SPLIT1",
            quantity=10.0,
            asset_class=AssetClass.EQUITY.value,
            account_platform="ICICI Direct",
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            current_value=5000.0,
            current_price_per_unit=500.0,
            user_id="sashank",
        )
        s.add(h)
        s.commit()
        s.refresh(h)
        s.add(
            InvestmentTransaction(
                txn_date=datetime.date(2023, 1, 1),
                symbol="SPLIT1",
                txn_type=InvestmentTxnType.BUY.value,
                quantity=10.0,
                price_per_unit=400.0,
                total_amount=4000.0,
                account_platform="ICICI Direct",
                holding_id=h.id,
            )
        )
        s.commit()

    r = client.get("/api/holdings?user_id=sashank")
    assert r.status_code == 200
    row = next(x for x in r.json() if x.get("symbol") == "SPLIT1")
    p = row.get("equity_holding_period")
    assert p is not None
    assert p["basis_note"] == "fifo_12m_india_listed_equity_cmp"
    assert p["long_term_value_inr"] == pytest.approx(5000.0)
    assert p["short_term_value_inr"] == pytest.approx(0.0)
    assert p["unallocated_value_inr"] == pytest.approx(0.0)


def test_list_holdings_defaults_to_active_only(client: TestClient, engine):
    with Session(engine) as s:
        a = Holding(
            name="Active MF",
            symbol="111",
            quantity=1.0,
            asset_class=AssetClass.MUTUAL_FUND.value,
            account_platform="ICICI Direct MF",
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_3.value,
            current_value=100.0,
            user_id="sashank",
            is_active=True,
        )
        z = Holding(
            name="Zombie Row",
            symbol="222",
            quantity=1.0,
            asset_class=AssetClass.MUTUAL_FUND.value,
            account_platform="ICICI Direct MF",
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_3.value,
            current_value=50.0,
            user_id="sashank",
            is_active=False,
        )
        s.add(a)
        s.add(z)
        s.commit()

    r = client.get("/api/holdings?user_id=sashank")
    assert r.status_code == 200
    names = {row["name"] for row in r.json()}
    assert names == {"Active MF"}

    r2 = client.get("/api/holdings?user_id=sashank&include_inactive=true")
    assert r2.status_code == 200
    names2 = {row["name"] for row in r2.json()}
    assert names2 == {"Active MF", "Zombie Row"}


def test_holdings_b3_summary_batch_returns_and_trend(client: TestClient, engine):
    """B3 — extended summary, batch XIRR payload, portfolio value trend (total_assets)."""
    with Session(engine) as s:
        s.add(
            Holding(
                name="B3 Equity A",
                symbol="B3A",
                quantity=10.0,
                average_cost_per_unit=100.0,
                asset_class=AssetClass.EQUITY.value,
                account_platform="Test",
                valuation_method=ValuationMethod.MANUAL.value,
                liquidity_class=LiquidityClass.T_PLUS_1.value,
                current_value=1200.0,
                user_id="sashank",
            )
        )
        s.add(
            Holding(
                name="B3 Equity B",
                symbol="B3B",
                quantity=1.0,
                average_cost_per_unit=100.0,
                asset_class=AssetClass.EQUITY.value,
                account_platform="Test",
                valuation_method=ValuationMethod.MANUAL.value,
                liquidity_class=LiquidityClass.T_PLUS_1.value,
                current_value=80.0,
                user_id="sashank",
            )
        )
        s.commit()

    summ = client.get("/api/holdings/summary?user_id=sashank")
    assert summ.status_code == 200
    sj = summ.json()
    assert sj["total_portfolio_value"] == pytest.approx(1280.0)
    assert sj["total_cost_basis"] == pytest.approx(1100.0)
    assert sj["total_overall_gain"] == pytest.approx(180.0)
    assert sj["total_overall_gain_pct"] == pytest.approx(100.0 * 180.0 / 1100.0, rel=1e-3)
    ac = sj["asset_class_breakdown"][AssetClass.EQUITY.value]
    assert ac["investment"] == pytest.approx(1100.0)
    assert ac["current_value"] == pytest.approx(1280.0)
    assert ac["overall_gain"] == pytest.approx(180.0)

    br = client.get("/api/holdings/batch-returns?user_id=sashank")
    assert br.status_code == 200
    bj = br.json()["returns"]
    assert len(bj) == 2
    for _hid, payload in bj.items():
        assert "method" in payload

    tr = client.get("/api/holdings/portfolio-value-trend?user_id=sashank&range=12M")
    assert tr.status_code == 200
    tj = tr.json()
    assert tj["granularity"] == "monthly"
    assert tj["range"] == "12M"
    assert len(tj["points"]) >= 1
    assert "total_portfolio_value" in tj["points"][0]
    assert "by_asset_class" in tj["points"][0]
    assert isinstance(tj["points"][0]["by_asset_class"], dict)
    # First month has no prior point → no % change.
    assert tj["points"][0]["pct_change_vs_prior_month"] is None


def test_create_holding_validation_rejects_bad_rate(client: TestClient):
    payload = {
        "name": "Bad rate",
        "asset_class": AssetClass.EQUITY.value,
        "account_platform": "X",
        "valuation_method": ValuationMethod.FIXED_RETURN.value,
        "liquidity_class": LiquidityClass.T_PLUS_1.value,
        "interest_rate": 150.0,
    }
    r = client.post("/api/holdings", json=payload)
    assert r.status_code == 422


def test_patch_holding_manual_only(client: TestClient, engine):
    with Session(engine) as s:
        hid = _seed_holding(s)

    r = client.patch(
        f"/api/holdings/{hid}?user_id=sashank",
        json={"current_value": 26_000.0},
    )
    assert r.status_code == 200
    assert r.json()["current_value"] == pytest.approx(26_000.0)


def test_liabilities_crud_and_summary(client: TestClient, engine):
    payload = {
        "name": "Bike",
        "liability_type": LiabilityType.SECURED_LOAN.value,
        "principal_outstanding": 200_000.0,
        "interest_rate": 9.0,
        "emi_amount": 8000.0,
        "user_id": "sashank",
    }
    r = client.post("/api/liabilities/", json=payload)
    assert r.status_code == 201
    lid = r.json()["id"]

    g = client.get(f"/api/liabilities/{lid}?user_id=sashank")
    assert g.status_code == 200

    s = client.get("/api/liabilities/summary?user_id=sashank")
    assert s.status_code == 200
    assert s.json()["principal_outstanding"] == pytest.approx(200_000.0)


def test_investment_transaction_create_and_list(client: TestClient, engine):
    with Session(engine) as s:
        hid = _seed_holding(s)

    body = {
        "txn_date": "2025-02-01",
        "symbol": "TESTAPI",
        "txn_type": InvestmentTxnType.BUY.value,
        "quantity": 1.0,
        "price_per_unit": 100.0,
        "total_amount": 100.0,
        "account_platform": "Unit",
        "holding_id": hid,
    }
    r = client.post("/api/investment-transactions/", json=body)
    assert r.status_code == 201

    lst = client.get("/api/investment-transactions", params={"holding_id": hid})
    assert lst.status_code == 200
    assert len(lst.json()) == 1


def test_investment_transactions_user_scoped_via_holding(client: TestClient, engine):
    """Rows for another user's holdings must not appear when user_id is set (F2.0)."""
    with Session(engine) as s:
        h_sash = Holding(
            name="Sash Equity",
            symbol="SASH1",
            quantity=1.0,
            asset_class=AssetClass.EQUITY.value,
            account_platform="Test",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            current_value=1000.0,
            user_id="sashank",
        )
        h_other = Holding(
            name="Partner Equity",
            symbol="PART1",
            quantity=1.0,
            asset_class=AssetClass.EQUITY.value,
            account_platform="Test",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            current_value=2000.0,
            user_id="partner",
        )
        s.add(h_sash)
        s.add(h_other)
        s.commit()
        s.refresh(h_sash)
        s.refresh(h_other)

    for hid, sym in [(h_sash.id, "SASH1"), (h_other.id, "PART1")]:
        r = client.post(
            "/api/investment-transactions/",
            json={
                "txn_date": "2025-02-01",
                "symbol": sym,
                "txn_type": InvestmentTxnType.BUY.value,
                "quantity": 1.0,
                "price_per_unit": 100.0,
                "total_amount": 100.0,
                "account_platform": "Test",
                "holding_id": hid,
            },
        )
        assert r.status_code == 201

    scoped = client.get("/api/investment-transactions", params={"user_id": "sashank"})
    assert scoped.status_code == 200
    rows = scoped.json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SASH1"

    scoped_p = client.get("/api/investment-transactions", params={"user_id": "partner"})
    assert scoped_p.status_code == 200
    assert len(scoped_p.json()) == 1
    assert scoped_p.json()[0]["symbol"] == "PART1"


def test_investment_transactions_orphan_only_when_unscoped(client: TestClient, engine):
    """Rows with holding_id NULL are listed only when user_id filter is omitted."""
    with Session(engine) as s:
        s.add(
            InvestmentTransaction(
                txn_date=datetime.date(2025, 3, 1),
                symbol="ORPH",
                txn_type=InvestmentTxnType.BUY.value,
                quantity=1.0,
                price_per_unit=1.0,
                total_amount=1.0,
                account_platform="Test",
                holding_id=None,
            )
        )
        s.commit()

    unscoped = client.get("/api/investment-transactions")
    assert unscoped.status_code == 200
    assert any(r["symbol"] == "ORPH" for r in unscoped.json())

    scoped = client.get("/api/investment-transactions", params={"user_id": "sashank"})
    assert scoped.status_code == 200
    assert not any(r["symbol"] == "ORPH" for r in scoped.json())


@patch("api.routes.prices.refresh_all_prices")
def test_prices_refresh_endpoint(mock_refresh, client: TestClient):
    mock_refresh.return_value = {
        "as_of": "2025-03-01",
        "price_rows_upserted": 3,
        "holdings_updated": 2,
        "nse_symbols": ["A", "B"],
        "mf_codes": ["119551"],
        "international_yfinance_symbols": [],
    }
    r = client.post("/api/prices/refresh")
    assert r.status_code == 200
    data = r.json()
    assert data["price_rows_upserted"] == 3
    assert data["mf_codes"] == ["119551"]


def test_price_history_rejects_long_symbol(client: TestClient):
    sym = "X" * 80
    r = client.get(f"/api/prices/{sym}/history")
    assert r.status_code == 400


def test_price_history_returns_rows(client: TestClient, engine):
    with Session(engine) as s:
        s.add(
            Price(
                symbol="ZZTOP",
                date=datetime.date(2025, 1, 5),
                close_price=42.0,
                source="nse",
            )
        )
        s.commit()

    r = client.get("/api/prices/ZZTOP/history?start_date=2025-01-01&end_date=2025-01-31")
    assert r.status_code == 200
    pts = r.json()
    assert len(pts) == 1
    assert pts[0]["close_price"] == pytest.approx(42.0)


def test_holdings_import_multipart(client: TestClient, engine):
    """Smoke-test multipart import wiring (parser + ingest on tiny fixture)."""
    from pathlib import Path

    csv_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "holdings"
        / "icici_portfolio_summary_min.csv"
    )
    raw = csv_path.read_bytes()
    files = {"files": ("icici_portfolio_summary_min.csv", io.BytesIO(raw), "text/csv")}
    data = {"source": "icici_direct_equity", "user_id": "sashank"}
    r = client.post("/api/holdings/import", files=files, data=data)
    assert r.status_code == 200
    out = r.json()
    assert out["source"] == "icici_direct_equity"
    assert "holdings_stats" in out
