"""
Investment transactions API — Phase A.3.2 + Phase 5 review queue

List/filter (paginated), manual create, CSV import, and PATCH for review workflow.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, func, select

from api.database import get_session
from api.models import Holding, InvestmentTransaction
from api.services.holdings_sync import (
    ensure_holding_for_transaction,
    sync_holding_from_transactions,
)
from api.routes.ingest_utils import parser_input_path, saved_upload_directory
from pipeline.holding_parsers import HOLDING_PARSER_REGISTRY
from pipeline.holding_pipeline import ingest_investment_transactions
from pipeline.models import InvestmentTxnType

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_TXN = {e.value for e in InvestmentTxnType}
IMPORT_SOURCES = frozenset(HOLDING_PARSER_REGISTRY.keys())

# Rough cash-flow buckets for filter UI (mirrors dashboard "Inflow / Outflow").
_FLOW_IN_TYPES = frozenset(
    {
        InvestmentTxnType.BUY.value,
        InvestmentTxnType.SIP.value,
        InvestmentTxnType.SWITCH_IN.value,
        InvestmentTxnType.DIVIDEND.value,
    }
)
_FLOW_OUT_TYPES = frozenset(
    {
        InvestmentTxnType.SELL.value,
        InvestmentTxnType.SWITCH_OUT.value,
    }
)


class InvestmentTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None
    txn_date: datetime.date
    symbol: str | None
    txn_type: str
    quantity: float
    price_per_unit: float
    total_amount: float
    account_platform: str
    holding_id: int | None
    bank_transaction_id: int | None
    notes: str | None
    is_reviewed: bool
    source_type: str | None
    gmail_message_id: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PaginatedInvestmentTransactionsResponse(BaseModel):
    items: list[InvestmentTransactionOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class InvestmentTransactionCreate(BaseModel):
    txn_date: datetime.date
    symbol: str | None = Field(default=None, max_length=64)
    txn_type: str
    quantity: float = Field(gt=0)
    price_per_unit: float = Field(ge=0)
    total_amount: float = Field(ge=0)
    account_platform: str = Field(min_length=1, max_length=128)
    holding_id: int | None = None
    bank_transaction_id: int | None = None
    notes: str | None = Field(default=None, max_length=10_000)


class InvestmentTransactionUpdate(BaseModel):
    """User-editable fields (review queue + light corrections)."""

    is_reviewed: bool | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    symbol: str | None = Field(default=None, max_length=64)
    txn_type: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    price_per_unit: float | None = Field(default=None, ge=0)
    total_amount: float | None = Field(default=None, ge=0)
    txn_date: datetime.date | None = None
    holding_id: int | None = None


class BulkInvestmentUpdateRequest(BaseModel):
    ids: list[int]
    update: InvestmentTransactionUpdate


class ImportInvTxnResultOut(BaseModel):
    source: str
    investment_txn_stats: dict[str, Any]


def _default_user_id_for_inv_sync() -> str:
    return (os.environ.get("ARTH_USER_ID") or "sashank").strip() or "sashank"


def _inv_txn_user_id_for_sync(session: Session, row: InvestmentTransaction) -> str:
    if row.holding_id is not None:
        h = session.get(Holding, row.holding_id)
        if h:
            return h.user_id
    return _default_user_id_for_sync()


def _validate_inv_update(body: InvestmentTransactionUpdate, *, session: Session | None = None) -> None:
    data = body.model_dump(exclude_unset=True)
    tt = data.get("txn_type")
    if tt is not None and tt not in _VALID_TXN:
        raise HTTPException(status_code=400, detail=f"Invalid txn_type: {tt!r}")
    hid = data.get("holding_id")
    if hid is not None and session is not None:
        h = session.get(Holding, hid)
        if not h:
            raise HTTPException(status_code=400, detail=f"holding_id {hid} does not exist")


def _apply_inv_update(row: InvestmentTransaction, body: InvestmentTransactionUpdate) -> None:
    data = body.model_dump(exclude_unset=True)
    for key, val in data.items():
        setattr(row, key, val)
    row.updated_at = datetime.datetime.now(datetime.UTC)


def _build_inv_list_query(
    *,
    user_id: str | None,
    holding_id: int | None,
    txn_type: str | None,
    symbol: str | None,
    search: str | None,
    account_platform: str | None,
    flow: str | None,
    date_from: str | None,
    date_to: str | None,
    is_reviewed: bool | None,
):
    q = select(InvestmentTransaction)
    uid = user_id.strip() if user_id and user_id.strip() else None
    if uid is not None:
        on_holding = cast(
            ColumnElement[Any],
            InvestmentTransaction.holding_id == Holding.id,
        )
        q = q.join(Holding, on_holding).where(Holding.user_id == uid)
    if holding_id is not None:
        q = q.where(InvestmentTransaction.holding_id == holding_id)
    if txn_type is not None:
        q = q.where(InvestmentTransaction.txn_type == txn_type)
    if symbol is not None:
        q = q.where(InvestmentTransaction.symbol == symbol)
    sk = search.strip() if search and search.strip() else None
    if sk:
        term = f"%{sk}%"
        q = q.where(
            or_(
                col(InvestmentTransaction.symbol).ilike(term),
                col(InvestmentTransaction.notes).ilike(term),
            )
        )
    plat = account_platform.strip() if account_platform and account_platform.strip() else None
    if plat is not None:
        q = q.where(InvestmentTransaction.account_platform == plat)
    fk = flow.strip().upper() if flow and flow.strip() else None
    if fk == "INFLOW":
        q = q.where(col(InvestmentTransaction.txn_type).in_(_FLOW_IN_TYPES))
    elif fk == "OUTFLOW":
        q = q.where(col(InvestmentTransaction.txn_type).in_(_FLOW_OUT_TYPES))
    elif fk is not None:
        raise HTTPException(
            status_code=400,
            detail="Invalid flow (use INFLOW, OUTFLOW, or omit)",
        )
    if date_from:
        try:
            d0 = datetime.date.fromisoformat(date_from[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from")
        q = q.where(InvestmentTransaction.txn_date >= d0)
    if date_to:
        try:
            d1 = datetime.date.fromisoformat(date_to[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to")
        q = q.where(InvestmentTransaction.txn_date <= d1)
    if is_reviewed is not None:
        q = q.where(InvestmentTransaction.is_reviewed == is_reviewed)
    return q


@router.get("", response_model=PaginatedInvestmentTransactionsResponse)
def list_investment_transactions(
    *,
    session: Session = Depends(get_session),
    user_id: str | None = Query(
        default=None,
        description="When set, only rows linked to a holding owned by this user (via JOIN). "
        "Omit for an unscoped list (includes rows with no holding_id).",
    ),
    holding_id: int | None = None,
    txn_type: str | None = None,
    symbol: str | None = None,
    search: str | None = Query(
        default=None,
        description="Case-insensitive substring match on symbol and notes.",
    ),
    account_platform: str | None = Query(
        default=None,
        description="Exact match on broker / platform string.",
    ),
    flow: str | None = Query(
        default=None,
        description='Cash-flow bucket: "INFLOW" (buy, sip, dividend, …) or '
        '"OUTFLOW" (sell, switch out).',
    ),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD"),
    is_reviewed: bool | None = Query(
        default=None,
        description="Filter by review status (e.g. false for the review queue).",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
):
    q = _build_inv_list_query(
        user_id=user_id,
        holding_id=holding_id,
        txn_type=txn_type,
        symbol=symbol,
        search=search,
        account_platform=account_platform,
        flow=flow,
        date_from=date_from,
        date_to=date_to,
        is_reviewed=is_reviewed,
    )
    count_query = select(func.count()).select_from(q.subquery())
    total = session.exec(count_query).one()

    q = q.order_by(
        col(InvestmentTransaction.txn_date).desc(),
        col(InvestmentTransaction.id).desc(),
    )
    offset = (page - 1) * page_size
    q = q.offset(offset).limit(page_size)
    rows = list(session.exec(q).all())
    total_pages = max(1, (total + page_size - 1) // page_size)

    return PaginatedInvestmentTransactionsResponse(
        items=[InvestmentTransactionOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.patch("/bulk", response_model=dict)
def bulk_update_investment_transactions(
    body: BulkInvestmentUpdateRequest,
    *,
    session: Session = Depends(get_session),
):
    """Apply the same update to many investment transactions (e.g. mark all reviewed)."""
    _validate_inv_update(body.update, session=session)
    updated: list[int] = []
    not_found: list[int] = []
    holding_ids_to_sync: set[int] = set()
    for iid in body.ids:
        row = session.get(InvestmentTransaction, iid)
        if not row:
            not_found.append(iid)
            continue
        old_hid = row.holding_id
        _apply_inv_update(row, body.update)
        session.add(row)
        updated.append(iid)
        if old_hid is not None:
            holding_ids_to_sync.add(old_hid)
        if row.holding_id is not None:
            holding_ids_to_sync.add(row.holding_id)
    session.commit()
    for hid in sorted(holding_ids_to_sync):
        sync_holding_from_transactions(session, hid)
    if holding_ids_to_sync:
        session.commit()
    return {"updated": updated, "not_found": not_found}


@router.patch("/{inv_id}", response_model=InvestmentTransactionOut)
def update_investment_transaction(
    inv_id: int,
    body: InvestmentTransactionUpdate,
    *,
    session: Session = Depends(get_session),
):
    row = session.get(InvestmentTransaction, inv_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Investment transaction {inv_id} not found")
    _validate_inv_update(body, session=session)
    old_hid = row.holding_id
    _apply_inv_update(row, body)
    session.add(row)
    session.commit()
    session.refresh(row)
    to_sync: set[int] = set()
    if old_hid is not None:
        to_sync.add(old_hid)
    if row.holding_id is not None:
        to_sync.add(row.holding_id)
    for hid in sorted(to_sync):
        sync_holding_from_transactions(session, hid)
    if to_sync:
        session.commit()
    session.refresh(row)
    return row


@router.post("/", response_model=InvestmentTransactionOut, status_code=201)
def create_investment_transaction(
    body: InvestmentTransactionCreate, *, session: Session = Depends(get_session)
):
    if body.txn_type not in _VALID_TXN:
        raise HTTPException(
            status_code=400, detail=f"Invalid txn_type: {body.txn_type!r}"
        )
    today = datetime.datetime.now(datetime.UTC).date()
    if body.txn_date > today:
        raise HTTPException(status_code=400, detail="txn_date cannot be in the future")
    if body.holding_id is not None:
        h = session.get(Holding, body.holding_id)
        if not h:
            raise HTTPException(status_code=400, detail="holding_id does not exist")
    now = datetime.datetime.now(datetime.UTC)
    it = InvestmentTransaction(
        txn_date=body.txn_date,
        symbol=body.symbol,
        txn_type=body.txn_type,
        quantity=body.quantity,
        price_per_unit=body.price_per_unit,
        total_amount=body.total_amount,
        account_platform=body.account_platform.strip(),
        holding_id=body.holding_id,
        bank_transaction_id=body.bank_transaction_id,
        notes=body.notes,
        is_reviewed=True,
        source_type=None,
        gmail_message_id=None,
        updated_at=now,
    )
    session.add(it)
    session.commit()
    session.refresh(it)
    uid = _inv_txn_user_id_for_sync(session, it)
    if it.holding_id is None:
        ensure_holding_for_transaction(session, it, user_id=uid)
    session.flush()
    if it.holding_id is not None:
        sync_holding_from_transactions(session, it.holding_id)
        session.commit()
        session.refresh(it)
    return it


@router.post("/import", response_model=ImportInvTxnResultOut)
def import_investment_transactions(
    *,
    session: Session = Depends(get_session),
    source: str = Form(...),
    user_id: str = Form(default="sashank"),
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
        _holdings, txns = parser_cls().parse_path(path)

    stats = ingest_investment_transactions(
        session,
        txns,
        user_id=user_id.strip() or "sashank",
        dry_run=False,
    )
    logger.info("API investment txn import source=%s stats=%s", sk, stats)
    return ImportInvTxnResultOut(source=sk, investment_txn_stats=stats)
