"""
Transaction CRUD + search + filtering endpoints.

GET  /api/transactions       — list with filters, pagination, sorting
GET  /api/transactions/{id}  — single transaction by DB id
PATCH /api/transactions/{id} — update mutable fields on one transaction
PATCH /api/transactions/bulk — bulk update (e.g. mark multiple as reviewed)
"""

from __future__ import annotations

import datetime
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from api.database import get_session
from api.models import Transaction

router = APIRouter()


# ───────────────────────────────────────────────────────────────────────────
# Request / response schemas
# ───────────────────────────────────────────────────────────────────────────

class TransactionUpdate(BaseModel):
    """Fields that the user is allowed to manually correct."""
    counterparty: str | None = None
    counterparty_category: str | None = None
    txn_type: str | None = None
    spend_category: str | None = None  # NEED | WANT | SAVING | INVESTMENT
    notes: str | None = None
    is_reviewed: bool | None = None


class BulkUpdateRequest(BaseModel):
    """Apply the same update to multiple transaction IDs at once."""
    ids: list[int]
    update: TransactionUpdate


class SortField(str, Enum):
    txn_date = "txn_date"
    amount = "amount"
    created_at = "created_at"
    counterparty = "counterparty"


class SortOrder(str, Enum):
    asc = "asc"
    desc = "desc"


class PaginatedResponse(BaseModel):
    """Wrapper that includes pagination metadata alongside results."""
    items: list[dict]
    total: int
    page: int
    page_size: int
    total_pages: int


# ───────────────────────────────────────────────────────────────────────────
# GET /  — list transactions with filters
# ───────────────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse)
def list_transactions(
    # Date range
    date_from: datetime.date | None = Query(None, description="Inclusive start date"),
    date_to: datetime.date | None = Query(None, description="Inclusive end date"),
    # Filters
    account_id: str | None = Query(None),
    direction: str | None = Query(None, description="INFLOW or OUTFLOW"),
    category: str | None = Query(None, description="counterparty_category value"),
    txn_type: str | None = Query(None),
    is_reviewed: bool | None = Query(None),
    # Free-text search (matches counterparty or raw_description)
    search: str | None = Query(None, min_length=1),
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    # Sorting
    sort_by: SortField = Query(SortField.txn_date),
    sort_order: SortOrder = Query(SortOrder.desc),
    *,
    session: Session = Depends(get_session),
):
    """List transactions with optional filters, pagination, and sorting."""
    query = select(Transaction)

    # Apply filters
    if date_from:
        query = query.where(Transaction.txn_date >= date_from)
    if date_to:
        query = query.where(Transaction.txn_date <= date_to)
    if account_id:
        query = query.where(Transaction.account_id == account_id)
    if direction:
        query = query.where(Transaction.direction == direction.upper())
    if category:
        query = query.where(Transaction.counterparty_category == category)
    if txn_type:
        query = query.where(Transaction.txn_type == txn_type)
    if is_reviewed is not None:
        query = query.where(Transaction.is_reviewed == is_reviewed)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            col(Transaction.counterparty).ilike(pattern)
            | col(Transaction.raw_description).ilike(pattern)
        )

    # Count total before pagination (for metadata)
    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

    # Sorting
    sort_column = getattr(Transaction, sort_by.value)
    if sort_order == SortOrder.desc:
        query = query.order_by(col(sort_column).desc())
    else:
        query = query.order_by(col(sort_column).asc())

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    results = session.exec(query).all()

    total_pages = max(1, (total + page_size - 1) // page_size)

    return PaginatedResponse(
        items=[_txn_to_dict(t) for t in results],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ───────────────────────────────────────────────────────────────────────────
# PATCH /bulk  — bulk update  (MUST be before /{txn_id} so FastAPI doesn't
#                              try to parse "bulk" as an integer path param)
# ───────────────────────────────────────────────────────────────────────────

@router.patch("/bulk")
def bulk_update_transactions(
    body: BulkUpdateRequest,
    *,
    session: Session = Depends(get_session),
):
    """Apply the same update to multiple transactions at once."""
    updated = []
    not_found = []

    for txn_id in body.ids:
        txn = session.get(Transaction, txn_id)
        if not txn:
            not_found.append(txn_id)
            continue
        _apply_update(txn, body.update)
        session.add(txn)
        updated.append(txn_id)

    session.commit()
    return {"updated": updated, "not_found": not_found}


# ───────────────────────────────────────────────────────────────────────────
# GET /{id}  — single transaction
# ───────────────────────────────────────────────────────────────────────────

@router.get("/{txn_id}")
def get_transaction(txn_id: int, *, session: Session = Depends(get_session)):
    """Fetch a single transaction by its database ID."""
    txn = session.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found")
    return _txn_to_dict(txn)


# ───────────────────────────────────────────────────────────────────────────
# PATCH /{id}  — update mutable fields
# ───────────────────────────────────────────────────────────────────────────

@router.patch("/{txn_id}")
def update_transaction(
    txn_id: int,
    body: TransactionUpdate,
    *,
    session: Session = Depends(get_session),
):
    """Update user-editable fields on a single transaction."""
    txn = session.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found")

    _apply_update(txn, body)
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return _txn_to_dict(txn)


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _apply_update(txn: Transaction, update: TransactionUpdate) -> None:
    """Apply non-None fields from the update to the transaction."""
    import datetime as _dt

    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(txn, field, value)
    if update_data:
        txn.updated_at = _dt.datetime.now(_dt.UTC)


def _txn_to_dict(txn: Transaction) -> dict:
    """Convert a Transaction ORM object to a plain dict for JSON responses.

    We do this manually rather than relying on SQLModel's .model_dump()
    because date/datetime fields need explicit serialisation for JSON.
    """
    return {
        "id": txn.id,
        "content_hash": txn.content_hash,
        "txn_date": txn.txn_date.isoformat() if txn.txn_date else None,
        "account_id": txn.account_id,
        "source_statement": txn.source_statement,
        "direction": txn.direction,
        "amount": txn.amount,
        "currency": txn.currency,
        "txn_type": txn.txn_type,
        "channel": txn.channel,
        "upi_type": txn.upi_type,
        "counterparty": txn.counterparty,
        "counterparty_category": txn.counterparty_category,
        "spend_category": txn.spend_category,
        "raw_description": txn.raw_description,
        "ref_number": txn.ref_number,
        "closing_balance": txn.closing_balance,
        "value_date": txn.value_date.isoformat() if txn.value_date else None,
        "notes": txn.notes,
        "is_reviewed": txn.is_reviewed,
        "pipeline_run_id": txn.pipeline_run_id,
        "created_at": txn.created_at.isoformat() if txn.created_at else None,
        "updated_at": txn.updated_at.isoformat() if txn.updated_at else None,
    }
