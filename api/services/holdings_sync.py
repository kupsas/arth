"""
Keep ``Holding`` rows aligned with linked ``InvestmentTransaction`` history.

Call :func:`sync_holding_from_transactions` after any insert/update to investment
transactions. Use :func:`ensure_holding_for_transaction` to auto-create a holding
for orphan BUY/SIP rows on known platforms (ICICI Direct equity / MF).

NPS holdings are skipped (valuation comes from ``HoldingValueSnapshot``, not txns).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from sqlmodel import Session, col, select

from api.models import Holding, InvestmentTransaction
from api.services.holding_enrichment import enrich_single_equity_classification
from api.services.liquidity_service import compute_earliest_liquidity_date
from api.services.ppf_ledger_basis import ppf_net_contributions_from_ledger
from api.services.price_feed import canonical_nse_symbol
from pipeline.investment_txn_linking import extract_amfi_scheme_code
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod

logger = logging.getLogger(__name__)

# Mirror historical_portfolio market-replay set (NPS excluded — snapshot-based).
_MARKET_SYNC_ASSET_CLASSES = frozenset(
    {
        AssetClass.EQUITY.value,
        AssetClass.ESOP.value,
        AssetClass.GOLD.value,
        AssetClass.MUTUAL_FUND.value,
        AssetClass.SOVEREIGN_GOLD_BOND.value,
    }
)

_INFLOW_FOR_AVG = frozenset(
    {
        InvestmentTxnType.BUY.value,
        InvestmentTxnType.SIP.value,
        InvestmentTxnType.SWITCH_IN.value,
    }
)

_OUTFLOW_QTY = frozenset(
    {
        InvestmentTxnType.SELL.value,
        InvestmentTxnType.SWITCH_OUT.value,
    }
)

# Auto-create only for these platforms (matches linking / parsers).
_PLATFORM_ICICI_EQUITY = "ICICI Direct"
_PLATFORM_ICICI_MF = "ICICI Direct MF"

_AUTOCREATE_TXN_TYPES = frozenset(
    {
        InvestmentTxnType.BUY.value,
        InvestmentTxnType.SIP.value,
    }
)


def _position_quantity_delta(txn: InvestmentTransaction) -> float:
    """Units change from this txn (BUY/SIP/SWITCH_IN positive; SELL/SWITCH_OUT negative)."""
    tt = txn.txn_type
    if tt in _INFLOW_FOR_AVG:
        return float(txn.quantity)
    if tt in _OUTFLOW_QTY:
        return -float(txn.quantity)
    return 0.0


def _load_txns_for_holding(session: Session, holding_id: int) -> list[InvestmentTransaction]:
    return list(
        session.exec(
            select(InvestmentTransaction)
            .where(InvestmentTransaction.holding_id == holding_id)
            .order_by(col(InvestmentTransaction.txn_date), col(InvestmentTransaction.id))
        ).all()
    )


def _compute_market_qty_and_avg_cost(txns: list[InvestmentTransaction]) -> tuple[float, float | None]:
    """
    Weighted average cost per unit for remaining quantity.

    Buys increase qty and blend average; sells reduce qty only (average unchanged
    for remaining shares). DIVIDEND does not change qty.
    """
    qty = 0.0
    avg: float | None = None

    for txn in txns:
        tt = txn.txn_type
        if tt in _INFLOW_FOR_AVG:
            q = float(txn.quantity)
            amt = float(txn.total_amount)
            if q <= 0:
                continue
            if qty <= 1e-12:
                qty = q
                avg = amt / q
            else:
                assert avg is not None
                total_cost = qty * avg + amt
                qty += q
                avg = total_cost / qty if qty > 1e-12 else None
        elif tt in _OUTFLOW_QTY:
            q = float(txn.quantity)
            sell = min(qty, q)
            qty -= sell
            if qty < 1e-9:
                qty = 0.0
                avg = None
        # DIVIDEND / others: no quantity change for position size

    if qty <= 1e-9:
        return 0.0, None
    return qty, avg


def _compute_ppf_balance(txns: list[InvestmentTransaction]) -> float:
    """Same rules as ``historical_ppf_holding_value`` (live, all txns)."""
    balance = 0.0
    for txn in txns:
        if txn.txn_type in (InvestmentTxnType.BUY.value, InvestmentTxnType.DIVIDEND.value):
            balance += float(txn.total_amount)
        elif txn.txn_type == InvestmentTxnType.SELL.value:
            balance -= float(txn.total_amount)
    return max(balance, 0.0)


def sync_holding_from_transactions(session: Session, holding_id: int) -> dict[str, Any]:
    """
    Recompute holding fields from all linked investment transactions.

    Returns a small stats dict (``status``, ``asset_class``, …). Idempotent.
    """
    h = session.get(Holding, holding_id)
    if h is None:
        return {"status": "error", "reason": "holding_not_found", "holding_id": holding_id}

    if h.asset_class == AssetClass.NPS.value:
        return {"status": "skipped", "reason": "nps_snapshot_based", "holding_id": holding_id}

    txns = _load_txns_for_holding(session, holding_id)
    if not txns:
        # No ledger rows — leave holding as-is (manual / CSV-only positions).
        return {"status": "no_transactions", "holding_id": holding_id}

    now = datetime.datetime.now(datetime.UTC)
    h.updated_at = now

    if h.asset_class == AssetClass.PPF.value:
        balance = _compute_ppf_balance(txns)
        h.current_value = round(balance, 2)
        # Keep ``principal_amount`` aligned with contributions so APIs / UI that read
        # the row (and ``holding_cost_basis`` fallbacks) match the ledger.
        net_contrib = ppf_net_contributions_from_ledger(session, holding_id)
        if net_contrib is not None:
            h.principal_amount = net_contrib
        h.is_active = balance > 1e-6
        session.add(h)
        return {
            "status": "ok",
            "holding_id": holding_id,
            "asset_class": h.asset_class,
            "ppf_balance": h.current_value,
        }

    if h.asset_class not in _MARKET_SYNC_ASSET_CLASSES:
        return {"status": "skipped", "reason": "asset_class_not_synced", "holding_id": holding_id}

    qty, avg = _compute_market_qty_and_avg_cost(txns)
    h.quantity = qty if qty > 1e-9 else 0.0
    h.average_cost_per_unit = avg

    px = h.current_price_per_unit
    if px is not None and qty > 1e-9:
        h.current_value = round(float(qty) * float(px), 2)
    elif avg is not None and qty > 1e-9:
        # No mark yet — show cost as proxy until price_feed runs.
        h.current_value = round(float(qty) * float(avg), 2)
    else:
        h.current_value = 0.0 if qty <= 1e-9 else h.current_value

    h.is_active = qty > 1e-9
    session.add(h)
    return {
        "status": "ok",
        "holding_id": holding_id,
        "asset_class": h.asset_class,
        "quantity": h.quantity,
        "average_cost_per_unit": h.average_cost_per_unit,
    }


def _find_equity_holding(
    session: Session, *, user_id: str, platform: str, symbol: str
) -> Holding | None:
    sym = canonical_nse_symbol(symbol)
    return session.exec(
        select(Holding).where(
            Holding.user_id == user_id,
            Holding.account_platform == platform,
            Holding.symbol == sym,
            col(Holding.asset_class).in_(
                (
                    AssetClass.EQUITY.value,
                    AssetClass.ESOP.value,
                    AssetClass.GOLD.value,
                    AssetClass.SOVEREIGN_GOLD_BOND.value,
                )
            ),
        )
    ).first()


def _find_mf_holding_by_symbol(
    session: Session, *, user_id: str, platform: str, amfi_code: str
) -> Holding | None:
    code = amfi_code.strip()
    if not code:
        return None
    return session.exec(
        select(Holding).where(
            Holding.user_id == user_id,
            Holding.account_platform == platform,
            Holding.asset_class == AssetClass.MUTUAL_FUND.value,
            Holding.symbol == code,
        )
    ).first()


def ensure_holding_for_transaction(
    session: Session,
    txn: InvestmentTransaction,
    *,
    user_id: str,
) -> int | None:
    """
    If ``txn`` has no ``holding_id`` and is BUY/SIP on ICICI Direct or ICICI Direct MF,
    link to an existing holding or create one.

    Returns new ``holding_id`` if linked/created, else ``None``.
    """
    if txn.holding_id is not None:
        return txn.holding_id
    if txn.txn_type not in _AUTOCREATE_TXN_TYPES:
        return None

    platform = (txn.account_platform or "").strip()
    uid = user_id.strip()
    if not uid:
        raise ValueError("user_id is required")

    if platform == _PLATFORM_ICICI_EQUITY:
        if not txn.symbol or not str(txn.symbol).strip():
            logger.debug("ensure_holding: ICICI Direct txn %s has no symbol — skip", txn.id)
            return None
        sym = canonical_nse_symbol(txn.symbol)
        existing = _find_equity_holding(session, user_id=uid, platform=platform, symbol=sym)
        if existing and existing.id is not None:
            txn.holding_id = existing.id
            session.add(txn)
            return existing.id

        name = (txn.notes or "").strip().split("\n")[0].strip() if txn.notes else sym
        if not name:
            name = sym
        h = Holding(
            symbol=sym,
            name=name[:512],
            quantity=None,
            asset_class=AssetClass.EQUITY.value,
            account_platform=platform,
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            user_id=uid,
            is_active=True,
            notes="Auto-created from investment transaction ledger",
        )
        session.add(h)
        session.flush()
        if h.id is None:
            return None
        _today = datetime.datetime.now(datetime.UTC).date()
        h.earliest_liquidity_date = compute_earliest_liquidity_date(session, h, _today)
        session.add(h)
        enrich_single_equity_classification(session, h)
        txn.holding_id = h.id
        session.add(txn)
        return h.id

    if platform == _PLATFORM_ICICI_MF:
        code = (str(txn.symbol).strip() if txn.symbol else "") or extract_amfi_scheme_code(
            txn.notes or ""
        )
        if not code:
            logger.debug("ensure_holding: ICICI Direct MF txn %s has no AMFI code — skip", txn.id)
            return None
        existing = _find_mf_holding_by_symbol(session, user_id=uid, platform=platform, amfi_code=code)
        if existing and existing.id is not None:
            txn.holding_id = existing.id
            session.add(txn)
            return existing.id

        name = (txn.notes or "").strip().split("\n")[0].strip() if txn.notes else f"MF {code}"
        h = Holding(
            symbol=code,
            name=name[:512],
            quantity=None,
            asset_class=AssetClass.MUTUAL_FUND.value,
            account_platform=platform,
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_3.value,
            user_id=uid,
            is_active=True,
            notes="Auto-created from investment transaction ledger",
        )
        session.add(h)
        session.flush()
        if h.id is None:
            return None
        _today = datetime.datetime.now(datetime.UTC).date()
        h.earliest_liquidity_date = compute_earliest_liquidity_date(session, h, _today)
        session.add(h)
        txn.holding_id = h.id
        session.add(txn)
        return h.id

    return None


def sync_holdings_for_user(session: Session, user_id: str) -> dict[str, Any]:
    """Run :func:`sync_holding_from_transactions` for every holding owned by ``user_id``."""
    uid = user_id.strip()
    if not uid:
        raise ValueError("user_id is required")
    rows = list(
        session.exec(select(Holding).where(Holding.user_id == uid)).all()
    )
    results: list[dict[str, Any]] = []
    for h in rows:
        if h.id is None:
            continue
        results.append(sync_holding_from_transactions(session, h.id))
    return {
        "user_id": uid,
        "holdings_examined": len(results),
        "results": results,
    }


def sync_holding_ids_after_txn_writes(
    session: Session,
    holding_ids: list[int | None],
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Deduplicate and sync each holding_id. Ignores None entries.

    Used by API routes after commits when multiple txns may touch the same holding.
    """
    seen: set[int] = set()
    uid = (user_id or "").strip() or None
    synced: list[dict[str, Any]] = []
    for hid in holding_ids:
        if hid is None:
            continue
        if hid in seen:
            continue
        seen.add(hid)
        synced.append(sync_holding_from_transactions(session, hid))
    return {"synced": synced, "user_id": uid}
