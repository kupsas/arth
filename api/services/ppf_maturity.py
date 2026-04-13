"""Derive PPF statutory maturity from linked investment ledger rows."""

from __future__ import annotations

import datetime

from sqlmodel import Session, col, select

from api.models import InvestmentTransaction
from pipeline.models import AssetClass, InvestmentTxnType
from pipeline.ppf_maturity import ppf_statutory_maturity_date


def earliest_ppf_contribution_date(session: Session, holding_id: int) -> datetime.date | None:
    """First **BUY** (your contribution) on the ledger; ignores interest (DIVIDEND) and withdrawals."""
    t = session.exec(
        select(InvestmentTransaction)
        .where(
            InvestmentTransaction.holding_id == holding_id,
            InvestmentTransaction.txn_type == InvestmentTxnType.BUY.value,
        )
        .order_by(col(InvestmentTransaction.txn_date))
        .limit(1)
    ).first()
    return t.txn_date if t else None


def computed_ppf_maturity_date(session: Session, holding_id: int | None) -> datetime.date | None:
    """Statutory maturity from earliest contribution, or None if no BUY rows."""
    if holding_id is None:
        return None
    first = earliest_ppf_contribution_date(session, holding_id)
    if first is None:
        return None
    return ppf_statutory_maturity_date(first)


def effective_ppf_maturity_date(
    session: Session,
    *,
    holding_id: int | None,
    stored_maturity: datetime.date | None,
    asset_class: str,
) -> datetime.date | None:
    """PPF statutory maturity (15 years from FY-end of first subscription) when ledger allows.

    We **prefer** :func:`computed_ppf_maturity_date` over ``stored_maturity`` so a stale or
    wrongly imported DB date cannot stretch the maturity column / projection (e.g. ~20 years).
    If there are no BUY rows yet, fall back to the stored value from the holding row.
    """
    if asset_class != AssetClass.PPF.value:
        return stored_maturity
    computed = computed_ppf_maturity_date(session, holding_id)
    if computed is not None:
        return computed
    return stored_maturity
