"""
Transaction CRUD + search + filtering endpoints.

GET  /api/transactions       — list with filters, pagination, sorting
GET  /api/transactions/{id}  — single transaction by DB id
PATCH /api/transactions/{id} — update mutable fields on one transaction
PATCH /api/transactions/bulk — bulk update (e.g. mark multiple as reviewed)
"""

from __future__ import annotations

import datetime
import logging
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from api.auth import get_current_user
from api.database import get_session
from api.models import Transaction, UserMerchantRule
from api.services.query_helpers import _for_user

logger = logging.getLogger(__name__)

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
    exclude_from_analytics: bool | None = None
    exclusion_reason: str | None = None
    # When True with counterparty/category changes, persist a user_merchant_rules row.
    apply_to_future: bool | None = None


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
    review_confidence: str | None = Query(
        None,
        description="Filter by review_confidence tier: HIGH, MEDIUM, LOW",
    ),
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
    current_user: str = Depends(get_current_user),
):
    """List transactions with optional filters, pagination, and sorting."""
    query = _for_user(select(Transaction), current_user)

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
    if review_confidence:
        query = query.where(Transaction.review_confidence == review_confidence.upper())
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
    current_user: str = Depends(get_current_user),
):
    """Apply the same update to multiple transactions at once."""
    updated = []
    not_found = []

    for txn_id in body.ids:
        txn = session.get(Transaction, txn_id)
        if not txn or txn.user_id != current_user:
            not_found.append(txn_id)
            continue
        _apply_update(txn, body.update)
        session.add(txn)
        updated.append(txn_id)

    session.commit()
    logger.info(
        "Bulk edit applied — updated=%s · not found=%s",
        len(updated),
        len(not_found),
    )
    return {"updated": updated, "not_found": not_found}


# ───────────────────────────────────────────────────────────────────────────
# GET /{id}  — single transaction
# ───────────────────────────────────────────────────────────────────────────

@router.get("/{txn_id}")
def get_transaction(
    txn_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Fetch a single transaction by its database ID."""
    txn = session.get(Transaction, txn_id)
    if not txn or txn.user_id != current_user:
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
    current_user: str = Depends(get_current_user),
):
    """Update user-editable fields on a single transaction."""
    txn = session.get(Transaction, txn_id)
    if not txn or txn.user_id != current_user:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found")

    was_reviewed = bool(txn.is_reviewed)
    apply_future = body.apply_to_future is True
    _apply_update(txn, body)
    txn.classification_source = "USER_REVIEWED"
    session.add(txn)

    # Persist a keyword rule when the user marks reviewed (review queue / approve) or
    # explicitly opts in from the edit sheet (apply_to_future).
    learn_merchant = bool(
        txn.counterparty
        and txn.counterparty_category
        and (
            apply_future
            or (bool(txn.is_reviewed) and not was_reviewed)
        )
    )
    if learn_merchant:
        _upsert_learned_merchant_rule(session, current_user, txn)

    auto_approved_count = 0
    if bool(txn.is_reviewed) and not was_reviewed:
        auto_approved_count = _propagate_approval_to_similar(
            session,
            current_user,
            txn,
            exclude_id=txn_id,
        )

    session.commit()
    session.refresh(txn)
    logger.debug(
        "Transaction patched id=%s apply_future_rules=%s auto_propagated=%s",
        txn_id,
        apply_future,
        auto_approved_count,
    )
    out = _txn_to_dict(txn)
    out["auto_approved_count"] = auto_approved_count
    return out


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _apply_update(txn: Transaction, update: TransactionUpdate) -> None:
    """Apply non-None fields from the update to the transaction."""
    import datetime as _dt

    update_data = update.model_dump(exclude_unset=True)
    update_data.pop("apply_to_future", None)
    # Turning off analytics exclusion clears the reason (keeps DB tidy).
    if update_data.get("exclude_from_analytics") is False:
        update_data["exclusion_reason"] = None
    for field, value in update_data.items():
        setattr(txn, field, value)
    if update_data:
        txn.updated_at = _dt.datetime.now(_dt.UTC)


def upsert_user_merchant_correction_rule(
    session: Session,
    user_id: str,
    *,
    keyword: str,
    display_name: str,
    counterparty_category: str,
) -> None:
    """Insert or update a ``USER_CORRECTION`` merchant row (keyword is matched as substring in narrations)."""
    kw = (keyword or "").strip().upper()
    if len(kw) < 2:
        return
    cat = (counterparty_category or "").strip()
    if not cat:
        return
    disp = (display_name or kw).strip()
    existing = session.exec(
        select(UserMerchantRule).where(
            UserMerchantRule.user_id == user_id,
            UserMerchantRule.keyword == kw,
        )
    ).first()
    if existing:
        existing.display_name = disp
        existing.counterparty_category = cat
        existing.source = "USER_CORRECTION"
        session.add(existing)
        return
    session.add(
        UserMerchantRule(
            user_id=user_id,
            keyword=kw,
            display_name=disp,
            counterparty_category=cat,
            source="USER_CORRECTION",
        )
    )


def _propagate_approval_to_similar(
    session: Session,
    user_id: str,
    txn: Transaction,
    *,
    exclude_id: int,
) -> int:
    """Approve every other unreviewed row with the same merchant label and category.

    Used when the user reviews one transaction so duplicates in the queue disappear
    without repeated clicks.
    """
    if not txn.counterparty or not txn.counterparty_category:
        return 0
    rows = session.exec(
        select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.counterparty == txn.counterparty,
            Transaction.counterparty_category == txn.counterparty_category,
            col(Transaction.is_reviewed).is_(False),
            col(Transaction.id) != exclude_id,
        )
    ).all()
    touched = 0
    for row in rows:
        row.is_reviewed = True
        row.classification_source = "USER_REVIEWED"
        session.add(row)
        touched += 1
    return touched


def _upsert_learned_merchant_rule(
    session: Session,
    user_id: str,
    txn: Transaction,
) -> None:
    """Store a keyword rule from a corrected counterparty for future pipeline runs."""
    keyword = (txn.counterparty or "").strip().upper()
    if len(keyword) < 2:
        return
    cat = txn.counterparty_category
    if not cat:
        return
    upsert_user_merchant_correction_rule(
        session,
        user_id,
        keyword=keyword,
        display_name=(txn.counterparty or keyword).strip(),
        counterparty_category=cat,
    )


def _txn_to_dict(txn: Transaction) -> dict:
    """Convert a Transaction ORM object to a plain dict for JSON responses.

    We do this manually rather than relying on SQLModel's .model_dump()
    because date/datetime fields need explicit serialisation for JSON.
    """
    return {
        "id": txn.id,
        "content_hash": txn.content_hash,
        "user_id": txn.user_id,
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
        "classification_source": getattr(txn, "classification_source", None),
        "review_confidence": getattr(txn, "review_confidence", None),
        "raw_description": txn.raw_description,
        "ref_number": txn.ref_number,
        "closing_balance": txn.closing_balance,
        "value_date": txn.value_date.isoformat() if txn.value_date else None,
        "notes": txn.notes,
        "is_reviewed": txn.is_reviewed,
        "pipeline_run_id": txn.pipeline_run_id,
        "exclude_from_analytics": bool(getattr(txn, "exclude_from_analytics", False)),
        "exclusion_reason": getattr(txn, "exclusion_reason", None),
        "created_at": txn.created_at.isoformat() if txn.created_at else None,
        "updated_at": txn.updated_at.isoformat() if txn.updated_at else None,
    }
