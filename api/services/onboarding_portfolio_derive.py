"""
Onboarding — derive broker holdings from investment ledger rows and refresh snapshots.

Runs after Gmail broker backfill so :class:`~api.models.Holding` reflects FIFO-style
positions even when only :class:`~api.models.InvestmentTransaction` rows were ingested
(e.g. ICICI equity **transaction statement** PDFs emit txns only).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from sqlalchemy import or_
from sqlmodel import Session, col, select

from api.models import Holding, HoldingValueSnapshot, InvestmentTransaction
from api.services.holding_enrichment import enrich_single_equity_classification
from parsers.holdings.base import ParsedInvestmentTxn
from parsers.holdings.derived_equity import derive_equity_holdings
from parsers.holdings.icici_direct_mf import derive_mf_holdings
from pipeline.holding_pipeline import ingest_holdings
from pipeline.investment_txn_linking import link_unlinked_investment_transactions, parse_mf_txn_notes
from pipeline.models import AssetClass

logger = logging.getLogger(__name__)

_BROKER_EQUITY_PLATFORM = "ICICI Direct"
_BROKER_MF_PLATFORM = "ICICI Direct MF"


def _inv_txn_to_parsed(row: InvestmentTransaction) -> ParsedInvestmentTxn:
    """Rebuild :class:`ParsedInvestmentTxn` from a persisted ledger row (for derivation)."""
    name = ""
    if row.notes and str(row.notes).strip():
        name = str(row.notes).split("\n")[0].strip()
    if not name:
        name = (row.symbol or "—").strip() or "Investment"
    metadata: dict[str, Any] = {}
    if (row.account_platform or "").strip() == _BROKER_MF_PLATFORM:
        folio, _hint = parse_mf_txn_notes(row.notes)
        if folio:
            metadata["folio"] = folio
        amfi = row.symbol if row.symbol and str(row.symbol).isdigit() else None
        if amfi:
            metadata["amfi_scheme_code"] = amfi
    return ParsedInvestmentTxn(
        txn_date=row.txn_date,
        symbol=row.symbol,
        name=name,
        txn_type=row.txn_type,
        quantity=row.quantity,
        price_per_unit=row.price_per_unit,
        total_amount=row.total_amount,
        account_platform=row.account_platform,
        notes=row.notes,
        metadata=metadata,
    )


def _load_linked_investment_transactions(session: Session, user_id: str) -> list[InvestmentTransaction]:
    """All ledger rows tied to this user's holdings (post ``link_unlinked``)."""
    hid_subq = select(Holding.id).where(Holding.user_id == user_id)
    return list(
        session.exec(
            select(InvestmentTransaction)
            .where(col(InvestmentTransaction.holding_id).in_(hid_subq))
            .order_by(col(InvestmentTransaction.txn_date), col(InvestmentTransaction.id))
        ).all()
    )


def run_onboarding_portfolio_derivation(session: Session, user_id: str) -> dict[str, Any]:
    """Link orphans, derive MF/equity :class:`ParsedHolding` rows from ledger history, upsert.

    Idempotent: :func:`pipeline.holding_pipeline.ingest_holdings` matches existing rows.
    """
    stats = link_unlinked_investment_transactions(session, user_ids=[user_id])
    rows = _load_linked_investment_transactions(session, user_id)
    parsed = [_inv_txn_to_parsed(r) for r in rows]

    eq_txns = [p for p in parsed if (p.account_platform or "").strip() == _BROKER_EQUITY_PLATFORM]
    mf_txns = [p for p in parsed if (p.account_platform or "").strip() == _BROKER_MF_PLATFORM]

    ph_eq = derive_equity_holdings(eq_txns) if eq_txns else []
    ph_mf = derive_mf_holdings(mf_txns) if mf_txns else []
    combined = ph_eq + ph_mf

    ingest_stats: dict[str, int] = {"inserted": 0, "updated": 0}
    if combined:
        ingest_stats = ingest_holdings(session, combined, user_id=user_id, dry_run=False)

    # Point-in-time marks for historical chart seeding (best-effort).
    today = datetime.date.today()
    n_snap = 0
    br_rows = list(
        session.exec(
            select(Holding)
            .where(Holding.user_id == user_id)
            .where(col(Holding.account_platform).in_((_BROKER_EQUITY_PLATFORM, _BROKER_MF_PLATFORM)))
            .where(Holding.is_active == True)  # noqa: E712
        ).all()
    )
    for h in br_rows:
        if h.id is None:
            continue
        val = h.current_value
        if val is None or val <= 0:
            continue
        ex = session.exec(
            select(HoldingValueSnapshot).where(
                HoldingValueSnapshot.holding_id == h.id,
                HoldingValueSnapshot.snapshot_date == today,
            )
        ).first()
        if ex is None:
            session.add(
                HoldingValueSnapshot(
                    holding_id=h.id,
                    snapshot_date=today,
                    value=float(val),
                    source="onboarding",
                    notes="post-import derivation",
                )
            )
            n_snap += 1
        else:
            ex.value = float(val)
            ex.source = "onboarding"
            session.add(ex)
        if h.asset_class == AssetClass.EQUITY.value:
            enrich_single_equity_classification(session, h)

    session.commit()

    out = {
        "link_stats": stats,
        "derived_equity_positions": len(ph_eq),
        "derived_mf_positions": len(ph_mf),
        "ingest_inserted": ingest_stats.get("inserted", 0),
        "ingest_updated": ingest_stats.get("updated", 0),
        "snapshots_upserted": n_snap,
    }
    logger.info(
        "Setup: Holdings snapshot updated from your imported statements.",
    )
    logger.debug(
        "Onboarding portfolio derivation detail — user_id=%s equity=%s mf=%s "
        "ingest_inserted=%s ingest_updated=%s snapshots=%s link_stats=%s",
        user_id,
        out["derived_equity_positions"],
        out["derived_mf_positions"],
        out["ingest_inserted"],
        out["ingest_updated"],
        out["snapshots_upserted"],
        stats,
    )
    return out


def portfolio_snapshot_summary(session: Session, user_id: str) -> dict[str, Any]:
    """Counts + top holdings for onboarding UI (broker slice only)."""
    stmt = (
        select(Holding)
        .where(Holding.user_id == user_id)
        .where(Holding.is_active == True)  # noqa: E712
        .where(
            or_(
                col(Holding.account_platform) == _BROKER_EQUITY_PLATFORM,
                col(Holding.account_platform) == _BROKER_MF_PLATFORM,
            ),
        )
        .order_by(col(Holding.current_value).desc())
    )
    rows = list(session.exec(stmt).all())
    total = sum(float(h.current_value or 0) for h in rows)
    top = []
    for h in rows[:12]:
        top.append(
            {
                "id": h.id,
                "name": h.name,
                "symbol": h.symbol,
                "asset_class": h.asset_class,
                "account_platform": h.account_platform,
                "quantity": h.quantity,
                "current_value": float(h.current_value or 0),
            }
        )
    return {
        "holding_count": len(rows),
        "equity_count": sum(1 for h in rows if h.asset_class == AssetClass.EQUITY.value),
        "mf_count": sum(1 for h in rows if h.asset_class == AssetClass.MUTUAL_FUND.value),
        "total_value_inr": round(total, 2),
        "top_holdings": top,
    }
