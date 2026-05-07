"""Re-apply merchant keyword rules to existing email rows after onboarding classify."""

from __future__ import annotations

import datetime
from decimal import Decimal
from enum import Enum
from typing import Iterable, TypeVar

from sqlalchemy import and_, func, or_
from sqlmodel import Session, col, select

from api.models import Transaction
from api.services.user_classification import load_user_classification_config
from pipeline.models import (
    CanonicalTransaction,
    Channel,
    ClassificationSource,
    CounterpartyCategory,
    Direction,
    SpendCategory,
    TxnType,
    UPIType,
)
from pipeline.rules_classifier import classify_rules


def _narration_matches_keyword_sql(kw: str):
    """SQL predicate: upper(raw_description) contains full ``kw`` OR (multi-word) every token ≥2 chars.

    Bank UPI narrations often omit a contiguous ``FIRST LAST`` substring (extra
    punctuation, truncation, VPA-only lines) while still containing both parts
    separately — widen matching for propagation only.
    """
    desc_u = func.upper(Transaction.raw_description)
    tokens = [t for t in kw.strip().upper().split() if len(t) >= 2]
    if not tokens:
        return func.instr(desc_u, kw) > 0
    if len(tokens) == 1:
        return func.instr(desc_u, tokens[0]) > 0
    full = func.instr(desc_u, kw) > 0
    all_parts = and_(*[func.instr(desc_u, t) > 0 for t in tokens])
    return or_(full, all_parts)


_E = TypeVar("_E", bound=Enum)


def _enum_member(enum_cls: type[_E], raw: str | None) -> _E | None:
    if not raw or not str(raw).strip():
        return None
    try:
        return enum_cls(str(raw).strip())
    except ValueError:
        return None


def transaction_to_canonical(row: Transaction) -> CanonicalTransaction:
    """Build a pipeline row from a DB transaction (for ``classify_rules`` only)."""
    tid = int(row.id or 0)
    if tid <= 0:
        tid = 1
    txn_id = f"T_{tid % 100_000_000:08d}"
    return CanonicalTransaction(
        txn_id=txn_id,
        txn_date=row.txn_date,
        account_id=row.account_id,
        source_statement=row.source_statement,
        direction=Direction(row.direction),
        amount=Decimal(str(row.amount)),
        currency=row.currency or "INR",
        txn_type=_enum_member(TxnType, row.txn_type),
        channel=_enum_member(Channel, row.channel),
        upi_type=_enum_member(UPIType, row.upi_type),
        counterparty=row.counterparty,
        counterparty_category=_enum_member(CounterpartyCategory, row.counterparty_category),
        spend_category=_enum_member(SpendCategory, row.spend_category),
        classification_source=_enum_member(ClassificationSource, row.classification_source),
        raw_description=row.raw_description,
        ref_number=row.ref_number,
        closing_balance=(
            Decimal(str(row.closing_balance)) if row.closing_balance is not None else None
        ),
        value_date=row.value_date,
        notes=row.notes,
    )


def _write_canonical_classification_back(row: Transaction, c: CanonicalTransaction) -> None:
    """Persist classification fields from a canonical row onto ``Transaction``."""
    row.txn_type = c.txn_type.value if c.txn_type else None
    row.channel = c.channel.value if c.channel else None
    row.upi_type = c.upi_type.value if c.upi_type else None
    row.counterparty = c.counterparty
    row.counterparty_category = (
        c.counterparty_category.value if c.counterparty_category else None
    )
    row.spend_category = c.spend_category.value if c.spend_category else None
    row.classification_source = (
        c.classification_source.value if c.classification_source else None
    )
    row.updated_at = datetime.datetime.now(datetime.UTC)


def propagate_merchant_keyword_hits(
    session: Session,
    user_id: str,
    *,
    keywords: Iterable[str],
    exclude_txn_ids: set[int],
) -> int:
    """Re-run :func:`pipeline.rules_classifier.classify_rules` on email rows whose narration contains a keyword.

    Skips rows the user already reviewed (``classification_source == USER_REVIEWED``) and any
    ``exclude_txn_ids`` from the current classify batch. Intended to run right after
    ``POST /api/onboarding/classify`` commits new ``USER_CORRECTION`` merchant rules so
    sibling UPI / bank narrations pick up the same counterparty without a full re-import.
    """
    kws = sorted({(k or "").strip().upper() for k in keywords if len((k or "").strip()) >= 2})
    if not kws:
        return 0

    ucfg = load_user_classification_config(session, user_id)

    by_id: dict[int, Transaction] = {}
    for kw in kws:
        conds = [
            Transaction.user_id == user_id,
            Transaction.source_type == "email",
            or_(
                col(Transaction.classification_source).is_(None),
                col(Transaction.classification_source) != "USER_REVIEWED",
            ),
            _narration_matches_keyword_sql(kw),
        ]
        if exclude_txn_ids:
            conds.append(col(Transaction.id).not_in(exclude_txn_ids))
        rows = session.exec(select(Transaction).where(*conds)).all()
        for r in rows:
            rid = int(r.id or 0)
            if rid:
                by_id[rid] = r

    if not by_id:
        return 0

    touched = 0
    for row in by_id.values():
        canon = transaction_to_canonical(row)
        classify_rules([canon], ucfg)
        _write_canonical_classification_back(row, canon)
        # Sibling rows picked up the user's merchant rule — treat as reviewed like the
        # rows they confirmed in onboarding, so they do not reappear on the review banner.
        row.is_reviewed = True
        session.add(row)
        touched += 1
    return touched
