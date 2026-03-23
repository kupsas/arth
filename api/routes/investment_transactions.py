"""
Investment transactions API — Phase A.3.2

List/filter, manual create, and CSV import via the same parser registry as holdings.
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from api.database import get_session
from api.models import Holding, InvestmentTransaction
from api.routes.ingest_utils import parser_input_path, saved_upload_directory
from pipeline.holding_parsers import HOLDING_PARSER_REGISTRY
from pipeline.holding_pipeline import ingest_investment_transactions
from pipeline.models import InvestmentTxnType

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_TXN = {e.value for e in InvestmentTxnType}
IMPORT_SOURCES = frozenset(HOLDING_PARSER_REGISTRY.keys())


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
    created_at: datetime.datetime


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


class ImportInvTxnResultOut(BaseModel):
    source: str
    investment_txn_stats: dict[str, int]


@router.get("", response_model=list[InvestmentTransactionOut])
def list_investment_transactions(
    *,
    session: Session = Depends(get_session),
    holding_id: int | None = None,
    txn_type: str | None = None,
    symbol: str | None = None,
    date_from: str | None = Query(default=None, description="YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD"),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
):
    q = select(InvestmentTransaction)
    if holding_id is not None:
        q = q.where(InvestmentTransaction.holding_id == holding_id)
    if txn_type is not None:
        q = q.where(InvestmentTransaction.txn_type == txn_type)
    if symbol is not None:
        q = q.where(InvestmentTransaction.symbol == symbol)
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
    q = q.order_by(col(InvestmentTransaction.txn_date).desc(), col(InvestmentTransaction.id).desc())
    q = q.offset(offset).limit(limit)
    return list(session.exec(q).all())


@router.post("/", response_model=InvestmentTransactionOut, status_code=201)
def create_investment_transaction(body: InvestmentTransactionCreate, *, session: Session = Depends(get_session)):
    if body.txn_type not in _VALID_TXN:
        raise HTTPException(status_code=400, detail=f"Invalid txn_type: {body.txn_type!r}")
    today = datetime.datetime.now(datetime.UTC).date()
    if body.txn_date > today:
        raise HTTPException(status_code=400, detail="txn_date cannot be in the future")
    if body.holding_id is not None:
        h = session.get(Holding, body.holding_id)
        if not h:
            raise HTTPException(status_code=400, detail="holding_id does not exist")
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
    )
    session.add(it)
    session.commit()
    session.refresh(it)
    return it


@router.post("/import", response_model=ImportInvTxnResultOut)
def import_investment_transactions(
    *,
    session: Session = Depends(get_session),
    source: str = Form(...),
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

    stats = ingest_investment_transactions(session, txns, dry_run=False)
    logger.info("API investment txn import source=%s stats=%s", sk, stats)
    return ImportInvTxnResultOut(source=sk, investment_txn_stats=stats)
