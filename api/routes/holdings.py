"""
Holdings API — Phase A.3.1

List/filter, net-worth aggregates, history, single holding + returns,
manual CRUD, and statement import (same parsers as ``pipeline.holding_pipeline``).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from api.database import get_session
from api.models import Holding
from api.routes.ingest_utils import parser_input_path, saved_upload_directory
from api.services.holding_enrichment import enrich_holdings
from api.services.net_worth import (
    compute_asset_allocation,
    compute_concentration,
    compute_net_worth,
    compute_net_worth_history,
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
    user_id: str
    is_active: bool
    notes: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


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
    user_id: str = Field(default="sashank", min_length=1, max_length=64)
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=10_000)


class HoldingUpdate(BaseModel):
    """Allowed manual tweaks — intended for ``MANUAL`` marks (plan A.3.1)."""

    current_value: float | None = Field(default=None, ge=0)
    last_valued_date: datetime.date | None = None
    notes: str | None = Field(default=None, max_length=10_000)


class HoldingsSummaryOut(BaseModel):
    net_worth: dict[str, Any]
    allocation: dict[str, dict[str, float]]
    concentration: dict[str, float | str | None]


class NetWorthHistoryOut(BaseModel):
    points: list[dict[str, Any]]
    granularity: str


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


@router.get("", response_model=list[HoldingOut])
def list_holdings(
    *,
    session: Session = Depends(get_session),
    user_id: str = Query(default="sashank"),
    asset_class: str | None = None,
    account_platform: str | None = None,
    liquidity_class: str | None = None,
    is_active: bool | None = None,
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
    q = q.order_by(Holding.name)
    return list(session.exec(q).all())


@router.get("/summary", response_model=HoldingsSummaryOut)
def holdings_summary(
    *,
    session: Session = Depends(get_session),
    user_id: str = Query(default="sashank"),
    as_of: str | None = Query(default=None, description="YYYY-MM-DD; optional historical snapshot"),
):
    as_of_d = _parse_opt_date(as_of)
    return HoldingsSummaryOut(
        net_worth=compute_net_worth(session, as_of_date=as_of_d, user_id=user_id),
        allocation=compute_asset_allocation(session, as_of_date=as_of_d, user_id=user_id),
        concentration=compute_concentration(session, as_of_date=as_of_d, user_id=user_id),
    )


@router.get("/history", response_model=NetWorthHistoryOut)
def holdings_history(
    *,
    session: Session = Depends(get_session),
    user_id: str = Query(default="sashank"),
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
    pts = compute_net_worth_history(session, sd, ed, granularity=granularity, user_id=user_id)  # type: ignore[arg-type]
    return NetWorthHistoryOut(points=pts, granularity=granularity)


@router.post("/enrich", response_model=HoldingsEnrichOut)
def enrich_holdings_endpoint(
    *,
    session: Session = Depends(get_session),
    user_id: str = Query(default="sashank"),
):
    """Backfill ``sector``, ``market_cap_class``, ``fund_category``, ``fund_house`` (Phase B).

    Downloads AMFI NAVAll once, calls NSE meta per equity (throttled). Safe to re-run.
    """
    report = enrich_holdings(session, user_id=user_id.strip() or None)
    d = report.as_dict()
    return HoldingsEnrichOut(**d)


@router.get("/{holding_id}", response_model=HoldingDetailOut)
def get_holding(
    holding_id: int,
    *,
    session: Session = Depends(get_session),
    user_id: str = Query(default="sashank"),
):
    h = session.get(Holding, holding_id)
    if not h or h.user_id != user_id:
        raise HTTPException(status_code=404, detail="Holding not found")
    ret = compute_returns(holding_id, session)
    return HoldingDetailOut(holding=HoldingOut.model_validate(h), returns=ret)


@router.post("", response_model=HoldingOut, status_code=201)
def create_holding(body: HoldingCreate, *, session: Session = Depends(get_session)):
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
        user_id=body.user_id,
        is_active=body.is_active,
        notes=body.notes,
    )
    if body.folio_number:
        h.folio_number_encrypted = body.folio_number
    if body.account_identifier:
        h.account_identifier_encrypted = body.account_identifier
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


@router.patch("/{holding_id}", response_model=HoldingOut)
def patch_holding(
    holding_id: int,
    body: HoldingUpdate,
    *,
    session: Session = Depends(get_session),
    user_id: str = Query(default="sashank"),
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
    h.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


@router.post("/import", response_model=ImportResultOut)
def import_holdings(
    *,
    session: Session = Depends(get_session),
    source: str = Form(..., description="Registry key, e.g. icici_direct_equity"),
    user_id: str = Form(default="sashank"),
    skip_investment_txns: bool = Form(default=False),
    files: list[UploadFile] = File(...),
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

    hstats = ingest_holdings(session, holdings, user_id=user_id.strip() or "sashank", dry_run=False)
    if skip_investment_txns:
        tstats = {"inserted": 0, "skipped_duplicate": 0, "errors": 0}
    else:
        tstats = ingest_investment_transactions(
            session,
            txns,
            user_id=user_id.strip() or "sashank",
            dry_run=False,
        )

    logger.info("API holdings import source=%s user=%s h=%s t=%s", sk, user_id, hstats, tstats)
    return ImportResultOut(source=sk, holdings_stats=hstats, investment_txn_stats=tstats)
