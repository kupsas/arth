"""
Holdings API — Phase A.3.1

List/filter, net-worth aggregates, history, single holding + returns,
manual CRUD, and statement import (same parsers as ``pipeline.holding_pipeline``).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from api.auth import effective_user_id, get_current_user
from api.database import get_session
from api.models import Holding
from api.routes.ingest_utils import parser_input_path, saved_upload_directory
from api.services.holding_enrichment import enrich_holdings, enrich_single_equity_classification
from api.services.holdings_metrics import (
    asset_class_breakdown_and_totals,
    compute_batch_returns,
    earliest_user_holding_date,
    overall_gain_for_holding,
    portfolio_trend_start_date,
    total_portfolio_value,
)
from api.services.nps_exit_projection import (
    NPS_PROJECTION_STATIC_NOTE,
    nps_normal_exit_date,
    nps_projected_balance_at_normal_exit,
    nps_projection_annual_rate_percent,
    parse_subscriber_dob_from_env,
)
from api.services.ppf_ledger_basis import ppf_net_contributions_from_ledger
from api.services.ppf_maturity import earliest_ppf_contribution_date, effective_ppf_maturity_date
from api.services.ppf_projection import ppf_projected_balance_at_maturity
from api.services.ppf_reference_rate import get_ppf_reference_rate_for_projection
from api.services.net_worth import (
    compute_asset_allocation,
    compute_concentration,
    compute_net_worth,
    compute_net_worth_history,
    historical_asset_class_values,
    holding_value,
    net_worth_history_anchor_dates,
)
from api.services.equity_holding_period import (
    EquityHoldingPeriodSplit,
    batch_equity_holding_period_splits,
)
from api.services.returns_calculator import compute_returns
from pipeline.holding_parsers import HOLDING_PARSER_REGISTRY
from pipeline.holding_pipeline import ingest_holdings, ingest_investment_transactions
from pipeline.models import AssetClass, LiquidityClass, MutualFundType as MFTypeEnum, ValuationMethod

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_ASSET = {e.value for e in AssetClass}
_VALID_VAL = {e.value for e in ValuationMethod}
_VALID_LIQ = {e.value for e in LiquidityClass}
_VALID_MF = {e.value for e in MFTypeEnum}

IMPORT_SOURCES = frozenset(HOLDING_PARSER_REGISTRY.keys())


class EquityHoldingPeriodSplitOut(BaseModel):
    """FIFO + >12 calendar months (India listed equity) at holding CMP; see ``equity_holding_period`` service."""

    long_term_value_inr: float = 0.0
    short_term_value_inr: float = 0.0
    unallocated_value_inr: float = 0.0
    fifo_quantity_after_txns: float = 0.0
    basis_note: str = ""


class HoldingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None
    symbol: str | None
    name: str
    quantity: float | None
    asset_class: str
    account_platform: str
    valuation_method: str
    current_value: float | None
    last_valued_date: datetime.date | None
    liquidity_class: str
    currency: str
    average_cost_per_unit: float | None
    current_price_per_unit: float | None
    principal_amount: float | None
    interest_rate: float | None
    maturity_date: datetime.date | None
    compounding_frequency: str | None
    face_value: float | None
    coupon_rate: float | None
    coupon_frequency: str | None
    fund_type: str | None
    # Enriched classification (NSE / AMFI); optional until POST …/enrich.
    sector: str | None = None
    market_cap_class: str | None = None
    fund_category: str | None = None
    fund_house: str | None = None
    earliest_liquidity_date: datetime.date | None = None
    user_id: str
    is_active: bool
    notes: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # Computed for the holdings page (B3); filled by list/detail/create/patch.
    overall_gain: float | None = None
    overall_gain_pct: float | None = None
    weight_pct: float | None = None
    # PPF only — ledger + illustrative maturity (see ``_holding_out_with_metrics``).
    ppf_first_contribution_date: datetime.date | None = None
    ppf_projected_value_at_maturity: float | None = None
    ppf_projection_annual_rate_pct: float | None = None
    ppf_projection_rate_note: str | None = None
    # NPS — ``DOB`` in API env → 60th birthday exit + illustrative growth (see ``nps_exit_projection``).
    nps_projected_value_at_normal_exit: float | None = None
    nps_projection_annual_rate_pct: float | None = None
    nps_projection_note: str | None = None
    # EQUITY (MARKET_PRICE) — ledger FIFO split; null for other sleeves.
    equity_holding_period: EquityHoldingPeriodSplitOut | None = None


class HoldingDetailOut(BaseModel):
    holding: HoldingOut
    returns: dict[str, Any]


class HoldingCreate(BaseModel):
    """Manual create — mirrors core ``Holding`` fields (PII sent as plaintext; ORM encrypts)."""

    name: str = Field(min_length=1, max_length=512)
    symbol: str | None = Field(default=None, max_length=64)
    quantity: float | None = Field(default=None, ge=0)
    asset_class: str
    account_platform: str = Field(min_length=1, max_length=128)
    valuation_method: str
    liquidity_class: str
    currency: str = Field(default="INR", min_length=3, max_length=8)
    current_value: float | None = Field(default=None, ge=0)
    last_valued_date: datetime.date | None = None
    average_cost_per_unit: float | None = Field(default=None, ge=0)
    current_price_per_unit: float | None = Field(default=None, ge=0)
    principal_amount: float | None = Field(default=None, ge=0)
    interest_rate: float | None = Field(default=None, ge=0, le=100)
    maturity_date: datetime.date | None = None
    compounding_frequency: str | None = Field(default=None, max_length=32)
    face_value: float | None = Field(default=None, ge=0)
    coupon_rate: float | None = Field(default=None, ge=0, le=100)
    coupon_frequency: str | None = Field(default=None, max_length=32)
    fund_type: str | None = None
    folio_number: str | None = Field(default=None, max_length=128)
    account_identifier: str | None = Field(default=None, max_length=256)
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=10_000)
    earliest_liquidity_date: datetime.date | None = None


class HoldingUpdate(BaseModel):
    """Allowed manual tweaks — intended for ``MANUAL`` marks (plan A.3.1)."""

    current_value: float | None = Field(default=None, ge=0)
    last_valued_date: datetime.date | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    earliest_liquidity_date: datetime.date | None = None


class HoldingsSummaryOut(BaseModel):
    net_worth: dict[str, Any]
    allocation: dict[str, dict[str, float]]
    concentration: dict[str, float | str | None]
    # Portfolio-only metrics (Layer 1 holdings); not net of liabilities.
    total_portfolio_value: float
    total_cost_basis: float
    total_overall_gain: float | None
    total_overall_gain_pct: float | None
    asset_class_breakdown: dict[str, dict[str, float | None]]


class NetWorthHistoryOut(BaseModel):
    points: list[dict[str, Any]]
    granularity: str


class PortfolioValueTrendPoint(BaseModel):
    date: str
    total_portfolio_value: float
    pct_change_vs_prior_month: float | None
    # INR per AssetClass string; same valuation rules as total_portfolio_value.
    by_asset_class: dict[str, float] = Field(default_factory=dict)


class PortfolioValueTrendOut(BaseModel):
    """Total assets over time (holdings only), monthly anchors — for area chart."""

    range: str
    granularity: str = "monthly"
    points: list[PortfolioValueTrendPoint]


class BatchReturnsOut(BaseModel):
    """Holding id (string key) → same shape as ``compute_returns`` per holding."""

    returns: dict[str, dict[str, Any]]


class HoldingsEnrichOut(BaseModel):
    """Result of POST /enrich — classification backfill from AMFI + NSE."""

    ok: bool = True
    mutual_funds_updated: int
    mutual_funds_skipped_no_meta: int
    equities_sector_updated: int
    equities_sector_failed: int
    equities_cap_updated: int
    equities_cap_unknown_symbol: int


class ImportResultOut(BaseModel):
    source: str
    holdings_stats: dict[str, int]
    investment_txn_stats: dict[str, Any]


def _validate_holding_enums(asset_class: str, valuation_method: str, liquidity_class: str, fund_type: str | None) -> None:
    if asset_class not in _VALID_ASSET:
        raise HTTPException(status_code=400, detail=f"Invalid asset_class: {asset_class!r}")
    if valuation_method not in _VALID_VAL:
        raise HTTPException(status_code=400, detail=f"Invalid valuation_method: {valuation_method!r}")
    if liquidity_class not in _VALID_LIQ:
        raise HTTPException(status_code=400, detail=f"Invalid liquidity_class: {liquidity_class!r}")
    if fund_type is not None and fund_type not in _VALID_MF:
        raise HTTPException(status_code=400, detail=f"Invalid fund_type: {fund_type!r}")


def _parse_opt_date(s: str | None) -> datetime.date | None:
    if not s or not str(s).strip():
        return None
    try:
        return datetime.date.fromisoformat(str(s).strip()[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date (use YYYY-MM-DD): {s!r}")


def _holding_out_with_metrics(
    session: Session,
    h: Holding,
    portfolio_total_value: float,
    *,
    equity_period_by_holding_id: dict[int, EquityHoldingPeriodSplit] | None = None,
) -> HoldingOut:
    """ORM row → API model with overall gain and weight vs full portfolio."""
    base = HoldingOut.model_validate(h)
    mat = effective_ppf_maturity_date(
        session,
        holding_id=h.id,
        stored_maturity=base.maturity_date,
        asset_class=h.asset_class,
    )
    extra: dict[str, Any] = {}
    if mat != base.maturity_date:
        extra["maturity_date"] = mat

    if h.asset_class == AssetClass.PPF.value:
        first_buy = (
            earliest_ppf_contribution_date(session, h.id) if h.id is not None else None
        )
        rate_pct, rate_note = get_ppf_reference_rate_for_projection()
        balance = float(holding_value(session, h, None) or 0.0)
        today = datetime.datetime.now(datetime.UTC).date()
        projected: float | None = None
        if mat is not None and balance > 0:
            projected = ppf_projected_balance_at_maturity(
                balance_today=balance,
                maturity_date=mat,
                today=today,
                annual_rate_percent=rate_pct,
            )
        extra.update(
            {
                "ppf_first_contribution_date": first_buy,
                "ppf_projected_value_at_maturity": projected,
                "ppf_projection_annual_rate_pct": rate_pct,
                "ppf_projection_rate_note": rate_note,
            }
        )
        # Expose ledger-based contributions as ``principal_amount`` so clients that
        # only read this field (e.g. portfolio "Invested") match server gain math.
        if h.id is not None:
            net_pb = ppf_net_contributions_from_ledger(session, h.id)
            if net_pb is not None:
                extra["principal_amount"] = net_pb

    if h.asset_class == AssetClass.NPS.value:
        dob = parse_subscriber_dob_from_env()
        today = datetime.datetime.now(datetime.UTC).date()
        balance = float(holding_value(session, h, None) or 0.0)
        if dob is not None:
            exit_dt = nps_normal_exit_date(dob)
            extra["maturity_date"] = exit_dt
            rate_nps = nps_projection_annual_rate_percent()
            projected_nps = nps_projected_balance_at_normal_exit(
                balance_today=balance,
                exit_date=exit_dt,
                today=today,
                annual_rate_percent=rate_nps,
            )
            extra.update(
                {
                    "nps_projected_value_at_normal_exit": projected_nps,
                    "nps_projection_annual_rate_pct": rate_nps,
                    "nps_projection_note": NPS_PROJECTION_STATIC_NOTE,
                }
            )

    og, ogp = overall_gain_for_holding(session, h)
    cv = holding_value(session, h, None)
    wp = round(100.0 * cv / portfolio_total_value, 2) if portfolio_total_value > 0 else None

    eq_period: EquityHoldingPeriodSplitOut | None = None
    if (
        h.asset_class == AssetClass.EQUITY.value
        and h.valuation_method == ValuationMethod.MARKET_PRICE.value
        and h.id is not None
    ):
        if equity_period_by_holding_id is not None:
            raw = equity_period_by_holding_id.get(h.id)
        else:
            today = datetime.datetime.now(datetime.UTC).date()
            m = batch_equity_holding_period_splits(session, [h], as_of=today)
            raw = m.get(h.id)
        if raw is not None:
            eq_period = EquityHoldingPeriodSplitOut(
                long_term_value_inr=raw.long_term_value_inr,
                short_term_value_inr=raw.short_term_value_inr,
                unallocated_value_inr=raw.unallocated_value_inr,
                fifo_quantity_after_txns=raw.fifo_quantity_after_txns,
                basis_note=raw.basis_note,
            )

    return base.model_copy(
        update={
            **extra,
            "overall_gain": og,
            "overall_gain_pct": ogp,
            "weight_pct": wp,
            "equity_holding_period": eq_period,
        }
    )


@router.get("", response_model=list[HoldingOut])
def list_holdings(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
    asset_class: str | None = None,
    account_platform: str | None = None,
    liquidity_class: str | None = None,
    is_active: bool | None = None,
    include_inactive: bool = Query(
        default=False,
        description="If true, include archived holdings (is_active=false). Default: active only.",
    ),
):
    q = select(Holding).where(Holding.user_id == user_id)
    if asset_class is not None:
        q = q.where(Holding.asset_class == asset_class)
    if account_platform is not None:
        q = q.where(Holding.account_platform == account_platform)
    if liquidity_class is not None:
        q = q.where(Holding.liquidity_class == liquidity_class)
    if is_active is not None:
        q = q.where(Holding.is_active == is_active)
    elif not include_inactive:
        q = q.where(Holding.is_active == True)  # noqa: E712
    q = q.order_by(Holding.name)
    uid = user_id
    rows = list(session.exec(q).all())
    # Archived rows are not in total_portfolio_value(); mix active+inactive needs a local denominator.
    if include_inactive:
        total_v = sum(holding_value(session, h, None) for h in rows)
        if total_v <= 0:
            total_v = total_portfolio_value(session, uid)
    else:
        total_v = total_portfolio_value(session, uid)
    today = datetime.datetime.now(datetime.UTC).date()
    eq_splits = batch_equity_holding_period_splits(session, rows, as_of=today)
    return [
        _holding_out_with_metrics(
            session, h, total_v, equity_period_by_holding_id=eq_splits
        )
        for h in rows
    ]


@router.get("/summary", response_model=HoldingsSummaryOut)
def holdings_summary(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
    as_of: str | None = Query(default=None, description="YYYY-MM-DD; optional historical snapshot"),
):
    as_of_d = _parse_opt_date(as_of)
    uid = user_id
    tpv, tcb, tog, togp, breakdown = asset_class_breakdown_and_totals(session, uid)
    return HoldingsSummaryOut(
        net_worth=compute_net_worth(session, as_of_date=as_of_d, user_id=uid),
        allocation=compute_asset_allocation(session, as_of_date=as_of_d, user_id=uid),
        concentration=compute_concentration(session, as_of_date=as_of_d, user_id=uid),
        total_portfolio_value=tpv,
        total_cost_basis=tcb,
        total_overall_gain=tog,
        total_overall_gain_pct=togp,
        asset_class_breakdown=breakdown,
    )


@router.get("/history", response_model=NetWorthHistoryOut)
def holdings_history(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    granularity: str = Query(default="monthly", pattern="^(daily|weekly|monthly)$"),
):
    sd = _parse_opt_date(start_date)
    ed = _parse_opt_date(end_date)
    if sd is None or ed is None:
        raise HTTPException(status_code=400, detail="start_date and end_date are required (YYYY-MM-DD)")
    if sd > ed:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date")
    pts = compute_net_worth_history(
        session,
        sd,
        ed,
        granularity=cast(Literal["daily", "weekly", "monthly"], granularity),
        user_id=user_id,
    )
    return NetWorthHistoryOut(points=pts, granularity=granularity)


@router.get("/batch-returns", response_model=BatchReturnsOut)
def holdings_batch_returns(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
):
    """Returns metrics for every active holding in one round-trip (cached until holdings change)."""
    uid = user_id
    raw = compute_batch_returns(session, uid)
    return BatchReturnsOut(returns={str(k): v for k, v in raw.items()})


@router.get("/portfolio-value-trend", response_model=PortfolioValueTrendOut)
def portfolio_value_trend(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
    range_: str = Query(
        default="12M",
        alias="range",
        description="Window: 3M, 6M, 12M (default), or all",
        pattern="^(3M|6M|12M|all)$",
    ),
):
    """Monthly total portfolio value (assets only) for the holdings area chart.

    Each point is valued as of the **last calendar day** of that month; for the
    current month, the anchor is **today** (so it matches the live portfolio total).
    """
    uid = user_id
    end = datetime.datetime.now(datetime.UTC).date()
    start = portfolio_trend_start_date(end, range_)
    if range_ == "all":
        first = earliest_user_holding_date(session, uid)
        if first is not None:
            start = max(start, first)
    start = max(start, datetime.date(2000, 1, 1))
    if start > end:
        start = end - datetime.timedelta(days=30)
    anchors = net_worth_history_anchor_dates(start, end, "monthly")
    points: list[PortfolioValueTrendPoint] = []
    prev_val: float | None = None
    for d in anchors:
        by_ac = historical_asset_class_values(session, as_of_date=d, user_id=uid)
        v = round(sum(by_ac.values()), 2)
        pct: float | None = None
        if prev_val is not None and prev_val > 0:
            pct = round(100.0 * (v - prev_val) / prev_val, 2)
        points.append(
            PortfolioValueTrendPoint(
                date=d.isoformat(),
                total_portfolio_value=v,
                pct_change_vs_prior_month=pct,
                by_asset_class=by_ac,
            )
        )
        prev_val = v
    return PortfolioValueTrendOut(range=range_, granularity="monthly", points=points)


@router.post("/enrich", response_model=HoldingsEnrichOut)
def enrich_holdings_endpoint(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
):
    """Backfill ``sector``, ``market_cap_class``, ``fund_category``, ``fund_house`` (Phase B).

    Downloads AMFI NAVAll once, calls NSE meta per equity (throttled). Safe to re-run.
    """
    report = enrich_holdings(session, user_id=user_id)
    d = report.as_dict()
    return HoldingsEnrichOut(**d)


@router.get("/{holding_id}", response_model=HoldingDetailOut)
def get_holding(
    holding_id: int,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
):
    uid = user_id
    h = session.get(Holding, holding_id)
    if not h or h.user_id != uid:
        raise HTTPException(status_code=404, detail="Holding not found")
    total_v = total_portfolio_value(session, uid)
    ret = compute_returns(holding_id, session)
    today = datetime.datetime.now(datetime.UTC).date()
    eq_splits = batch_equity_holding_period_splits(session, [h], as_of=today)
    return HoldingDetailOut(
        holding=_holding_out_with_metrics(
            session, h, total_v, equity_period_by_holding_id=eq_splits
        ),
        returns=ret,
    )


@router.post("", response_model=HoldingOut, status_code=201)
def create_holding(
    body: HoldingCreate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    _validate_holding_enums(body.asset_class, body.valuation_method, body.liquidity_class, body.fund_type)
    today = datetime.datetime.now(datetime.UTC).date()
    if body.last_valued_date is not None and body.last_valued_date > today:
        raise HTTPException(status_code=400, detail="last_valued_date cannot be in the future")
    h = Holding(
        symbol=body.symbol,
        name=body.name.strip(),
        quantity=body.quantity,
        asset_class=body.asset_class,
        account_platform=body.account_platform.strip(),
        valuation_method=body.valuation_method,
        current_value=body.current_value,
        last_valued_date=body.last_valued_date,
        liquidity_class=body.liquidity_class,
        currency=body.currency or "INR",
        average_cost_per_unit=body.average_cost_per_unit,
        current_price_per_unit=body.current_price_per_unit,
        principal_amount=body.principal_amount,
        interest_rate=body.interest_rate,
        maturity_date=body.maturity_date,
        compounding_frequency=body.compounding_frequency,
        face_value=body.face_value,
        coupon_rate=body.coupon_rate,
        coupon_frequency=body.coupon_frequency,
        fund_type=body.fund_type,
        user_id=current_user,
        is_active=body.is_active,
        notes=body.notes,
        earliest_liquidity_date=body.earliest_liquidity_date,
    )
    if body.folio_number:
        h.folio_number_encrypted = body.folio_number
    if body.account_identifier:
        h.account_identifier_encrypted = body.account_identifier
    session.add(h)
    session.flush()
    enrich_single_equity_classification(session, h)
    session.commit()
    session.refresh(h)
    uid = current_user
    eq_splits = batch_equity_holding_period_splits(session, [h], as_of=today)
    total_v = total_portfolio_value(session, uid)
    return _holding_out_with_metrics(
        session, h, total_v, equity_period_by_holding_id=eq_splits
    )


@router.patch("/{holding_id}", response_model=HoldingOut)
def patch_holding(
    holding_id: int,
    body: HoldingUpdate,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
):
    h = session.get(Holding, holding_id)
    if not h or h.user_id != user_id:
        raise HTTPException(status_code=404, detail="Holding not found")
    if h.valuation_method != ValuationMethod.MANUAL.value:
        raise HTTPException(
            status_code=400,
            detail="PATCH is only supported for MANUAL valuation_method holdings",
        )
    today = datetime.datetime.now(datetime.UTC).date()
    if body.current_value is not None:
        h.current_value = body.current_value
    if body.last_valued_date is not None:
        if body.last_valued_date > today:
            raise HTTPException(status_code=400, detail="last_valued_date cannot be in the future")
        h.last_valued_date = body.last_valued_date
    if body.notes is not None:
        h.notes = body.notes
    patch_fields = body.model_dump(exclude_unset=True)
    if "earliest_liquidity_date" in patch_fields:
        h.earliest_liquidity_date = patch_fields["earliest_liquidity_date"]
    h.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(h)
    session.commit()
    session.refresh(h)
    total_v = total_portfolio_value(session, user_id)
    return _holding_out_with_metrics(session, h, total_v)


@router.post("/import", response_model=ImportResultOut)
def import_holdings(
    *,
    session: Session = Depends(get_session),
    source: str = Form(..., description="Registry key, e.g. icici_direct_equity"),
    skip_investment_txns: bool = Form(default=False),
    files: list[UploadFile] = File(...),
    current_user: str = Depends(get_current_user),
):
    sk = source.strip()
    if sk not in IMPORT_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown source {sk!r}. Valid: {sorted(IMPORT_SOURCES)}",
        )
    with saved_upload_directory(files) as td:
        path = parser_input_path(td)
        parser_cls = HOLDING_PARSER_REGISTRY[sk]
        holdings, txns = parser_cls().parse_path(path)

    hstats = ingest_holdings(session, holdings, user_id=current_user, dry_run=False)
    if skip_investment_txns:
        tstats = {"inserted": 0, "skipped_duplicate": 0, "errors": 0, "holdings_synced": 0}
    else:
        tstats = ingest_investment_transactions(
            session,
            txns,
            user_id=current_user,
            dry_run=False,
        )

    logger.info("API holdings import source=%s user=%s h=%s t=%s", sk, current_user, hstats, tstats)
    return ImportResultOut(source=sk, holdings_stats=hstats, investment_txn_stats=tstats)
