"""
Historical portfolio helpers for point-in-time valuation.

Rules:

- Market-priced sleeves replay quantities from linked ``investment_transactions``.
- PPF uses a ledger-style balance from contribution / interest / withdrawal rows.
- NPS uses dated statement snapshots imported from CRA files.
- Symbols in ``EXCLUDED_HISTORICAL_SYMBOLS`` are intentionally ignored for history.
- When a replayed market position has no price on or before the anchor date, it
  contributes ``0`` instead of falling back to today's mark.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlmodel import Session, col, select

from api.models import Holding, HoldingValueSnapshot, InvestmentTransaction, Price
from api.services.price_feed import canonical_nse_symbol
from pipeline.models import AssetClass, InvestmentTxnType, ValuationMethod

EXCLUDED_HISTORICAL_SYMBOLS = {"STOONE", "INDWHO"}

_MARKET_REPLAY_ASSET_CLASSES = {
    AssetClass.EQUITY.value,
    AssetClass.ESOP.value,
    AssetClass.GOLD.value,
    AssetClass.MUTUAL_FUND.value,
    AssetClass.SOVEREIGN_GOLD_BOND.value,
}

_POSITIVE_TXN_TYPES = {
    InvestmentTxnType.BUY.value,
    InvestmentTxnType.SIP.value,
    InvestmentTxnType.SWITCH_IN.value,
}

_NEGATIVE_TXN_TYPES = {
    InvestmentTxnType.SELL.value,
    InvestmentTxnType.SWITCH_OUT.value,
}

_PLATFORM_TO_MARKET_ASSET_CLASS = {
    "ICICI Direct": AssetClass.EQUITY.value,
    "ICICI Direct MF": AssetClass.MUTUAL_FUND.value,
}


@dataclass(frozen=True)
class PriceCoverageRow:
    symbol: str
    symbol_kind: str
    earliest_price_date: datetime.date | None
    latest_price_date: datetime.date | None
    has_price_on_or_before_start: bool
    has_price_on_or_before_end: bool


def is_excluded_historical_symbol(symbol: str | None) -> bool:
    if not symbol:
        return False
    return symbol.strip().upper() in EXCLUDED_HISTORICAL_SYMBOLS


def holding_created_on_or_before(h: Holding, as_of: datetime.date) -> bool:
    created = h.created_at
    if isinstance(created, datetime.datetime):
        created_date = created.date()
    elif isinstance(created, datetime.date):
        created_date = created
    else:
        return True
    return created_date <= as_of


def price_lookup_symbol(asset_class: str, symbol: str | None) -> str | None:
    if not symbol or is_excluded_historical_symbol(symbol):
        return None
    raw = symbol.strip()
    if not raw:
        return None
    if asset_class == AssetClass.MUTUAL_FUND.value and raw.isdigit():
        return raw
    if asset_class == AssetClass.GOLD.value and "=" in raw:
        return raw
    return canonical_nse_symbol(raw)


def is_market_replay_holding(h: Holding) -> bool:
    return (
        h.valuation_method == ValuationMethod.MARKET_PRICE.value
        and h.asset_class in _MARKET_REPLAY_ASSET_CLASSES
        and not is_excluded_historical_symbol(h.symbol)
    )


def _latest_price_on_or_before(
    session: Session,
    symbol: str,
    as_of: datetime.date,
) -> float | None:
    row = session.exec(
        select(Price.close_price)
        .where(Price.symbol == symbol, Price.date <= as_of)
        .order_by(col(Price.date).desc())
        .limit(1)
    ).first()
    return float(row) if row is not None else None


def _market_position_delta(txn: InvestmentTransaction) -> float:
    if txn.txn_type in _POSITIVE_TXN_TYPES:
        return float(txn.quantity)
    if txn.txn_type in _NEGATIVE_TXN_TYPES:
        return -float(txn.quantity)
    return 0.0


def _market_history_txn_rows(
    session: Session,
    *,
    user_id: str,
    as_of: datetime.date | None = None,
    holding_id: int | None = None,
) -> list[tuple[InvestmentTransaction, Holding]]:
    stmt = (
        select(InvestmentTransaction, Holding)
        .join(Holding, InvestmentTransaction.holding_id == Holding.id)
        .where(
            Holding.user_id == user_id,
            Holding.valuation_method == ValuationMethod.MARKET_PRICE.value,
            col(Holding.asset_class).in_(tuple(_MARKET_REPLAY_ASSET_CLASSES)),
        )
        .order_by(col(InvestmentTransaction.txn_date), col(InvestmentTransaction.id))
    )
    if as_of is not None:
        stmt = stmt.where(InvestmentTransaction.txn_date <= as_of)
    if holding_id is not None:
        stmt = stmt.where(Holding.id == holding_id)
    return list(session.exec(stmt).all())


def _platform_is_uniquely_owned_by_user(session: Session, *, platform: str, user_id: str) -> bool:
    owners = list(
        session.exec(
            select(Holding.user_id)
            .where(Holding.account_platform == platform)
            .distinct()
        ).all()
    )
    owners_norm = {str(owner).strip() for owner in owners if str(owner).strip()}
    return owners_norm == {user_id}


def _orphan_market_txn_rows(
    session: Session,
    *,
    user_id: str,
    as_of: datetime.date | None = None,
) -> list[tuple[InvestmentTransaction, str]]:
    rows: list[tuple[InvestmentTransaction, str]] = []
    for platform, asset_class in _PLATFORM_TO_MARKET_ASSET_CLASS.items():
        if not _platform_is_uniquely_owned_by_user(session, platform=platform, user_id=user_id):
            continue
        stmt = (
            select(InvestmentTransaction)
            .where(
                InvestmentTransaction.holding_id.is_(None),
                InvestmentTransaction.account_platform == platform,
                col(InvestmentTransaction.symbol).is_not(None),
            )
            .order_by(col(InvestmentTransaction.txn_date), col(InvestmentTransaction.id))
        )
        if as_of is not None:
            stmt = stmt.where(InvestmentTransaction.txn_date <= as_of)
        rows.extend((txn, asset_class) for txn in session.exec(stmt).all())
    return rows


def market_position_quantities_as_of(
    session: Session,
    *,
    user_id: str,
    as_of: datetime.date,
) -> dict[tuple[str, str], float]:
    positions: dict[tuple[str, str], float] = {}
    for txn, holding in _market_history_txn_rows(session, user_id=user_id, as_of=as_of):
        lookup = price_lookup_symbol(holding.asset_class, txn.symbol or holding.symbol)
        if lookup is None:
            continue
        delta = _market_position_delta(txn)
        if delta == 0:
            continue
        key = (holding.asset_class, lookup)
        next_qty = positions.get(key, 0.0) + delta
        if next_qty <= 1e-9:
            positions.pop(key, None)
            continue
        positions[key] = next_qty
    for txn, asset_class in _orphan_market_txn_rows(session, user_id=user_id, as_of=as_of):
        lookup = price_lookup_symbol(asset_class, txn.symbol)
        if lookup is None:
            continue
        delta = _market_position_delta(txn)
        if delta == 0:
            continue
        key = (asset_class, lookup)
        next_qty = positions.get(key, 0.0) + delta
        if next_qty <= 1e-9:
            positions.pop(key, None)
            continue
        positions[key] = next_qty
    return positions


def historical_market_assets_value(
    session: Session,
    *,
    user_id: str,
    as_of: datetime.date,
) -> float:
    total = 0.0
    positions = market_position_quantities_as_of(session, user_id=user_id, as_of=as_of)
    for (_asset_class, symbol), qty in positions.items():
        px = _latest_price_on_or_before(session, symbol, as_of)
        if px is None:
            continue
        total += qty * px
    return round(total, 2)


def historical_market_holding_value(
    session: Session,
    h: Holding,
    *,
    as_of: datetime.date,
) -> float:
    if h.id is None or not is_market_replay_holding(h):
        return 0.0
    if not holding_created_on_or_before(h, as_of):
        return 0.0
    lookup = price_lookup_symbol(h.asset_class, h.symbol)
    if lookup is None:
        return 0.0
    qty = 0.0
    for txn, _holding in _market_history_txn_rows(
        session,
        user_id=h.user_id,
        as_of=as_of,
        holding_id=h.id,
    ):
        qty += _market_position_delta(txn)
        if qty < 0:
            qty = 0.0
    if qty <= 1e-9:
        return 0.0
    px = _latest_price_on_or_before(session, lookup, as_of)
    if px is None:
        return 0.0
    return round(qty * px, 2)


def historical_ppf_holding_value(
    session: Session,
    h: Holding,
    *,
    as_of: datetime.date,
) -> float:
    if h.id is None or h.asset_class != AssetClass.PPF.value:
        return 0.0
    rows = list(
        session.exec(
            select(InvestmentTransaction)
            .where(
                InvestmentTransaction.holding_id == h.id,
                InvestmentTransaction.txn_date <= as_of,
            )
            .order_by(col(InvestmentTransaction.txn_date), col(InvestmentTransaction.id))
        ).all()
    )
    balance = 0.0
    for txn in rows:
        if txn.txn_type in (InvestmentTxnType.BUY.value, InvestmentTxnType.DIVIDEND.value):
            balance += float(txn.total_amount)
        elif txn.txn_type == InvestmentTxnType.SELL.value:
            balance -= float(txn.total_amount)
    return round(max(balance, 0.0), 2)


def historical_nps_holding_value(
    session: Session,
    h: Holding,
    *,
    as_of: datetime.date,
) -> float:
    if h.id is None or h.asset_class != AssetClass.NPS.value:
        return 0.0
    row = session.exec(
        select(HoldingValueSnapshot)
        .where(
            HoldingValueSnapshot.holding_id == h.id,
            HoldingValueSnapshot.snapshot_date <= as_of,
        )
        .order_by(col(HoldingValueSnapshot.snapshot_date).desc(), col(HoldingValueSnapshot.id).desc())
        .limit(1)
    ).first()
    if row is None:
        return 0.0
    return round(float(row.value), 2)


def historical_price_symbol_universe(session: Session, *, user_id: str) -> dict[str, list[str]]:
    nse_symbols: set[str] = set()
    mf_codes: set[str] = set()
    unsupported_symbols: set[str] = set()

    active_holdings = list(
        session.exec(
            select(Holding).where(
                Holding.user_id == user_id,
                Holding.is_active == True,  # noqa: E712
                Holding.valuation_method == ValuationMethod.MARKET_PRICE.value,
                col(Holding.asset_class).in_(tuple(_MARKET_REPLAY_ASSET_CLASSES)),
            )
        ).all()
    )
    for h in active_holdings:
        lookup = price_lookup_symbol(h.asset_class, h.symbol)
        if lookup is None:
            continue
        if h.asset_class == AssetClass.MUTUAL_FUND.value and lookup.isdigit():
            mf_codes.add(lookup)
        elif "=" in lookup:
            unsupported_symbols.add(lookup)
        else:
            nse_symbols.add(lookup)

    for txn, holding in _market_history_txn_rows(session, user_id=user_id):
        lookup = price_lookup_symbol(holding.asset_class, txn.symbol or holding.symbol)
        if lookup is None:
            continue
        if holding.asset_class == AssetClass.MUTUAL_FUND.value and lookup.isdigit():
            mf_codes.add(lookup)
        elif "=" in lookup:
            unsupported_symbols.add(lookup)
        else:
            nse_symbols.add(lookup)
    for txn, asset_class in _orphan_market_txn_rows(session, user_id=user_id):
        lookup = price_lookup_symbol(asset_class, txn.symbol)
        if lookup is None:
            continue
        if asset_class == AssetClass.MUTUAL_FUND.value and lookup.isdigit():
            mf_codes.add(lookup)
        elif "=" in lookup:
            unsupported_symbols.add(lookup)
        else:
            nse_symbols.add(lookup)

    return {
        "nse_symbols": sorted(nse_symbols),
        "mf_codes": sorted(mf_codes),
        "unsupported_symbols": sorted(unsupported_symbols),
    }


def price_coverage_report(
    session: Session,
    *,
    user_id: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[PriceCoverageRow]:
    universe = historical_price_symbol_universe(session, user_id=user_id)
    rows: list[PriceCoverageRow] = []
    for symbol in universe["mf_codes"] + universe["nse_symbols"] + universe["unsupported_symbols"]:
        all_rows = list(
            session.exec(
                select(Price)
                .where(Price.symbol == symbol)
                .order_by(col(Price.date).asc())
            ).all()
        )
        earliest = all_rows[0].date if all_rows else None
        latest = all_rows[-1].date if all_rows else None
        has_start = any(r.date <= start_date for r in all_rows)
        has_end = any(r.date <= end_date for r in all_rows)
        symbol_kind = "mf" if symbol.isdigit() else "other" if "=" in symbol else "nse"
        rows.append(
            PriceCoverageRow(
                symbol=symbol,
                symbol_kind=symbol_kind,
                earliest_price_date=earliest,
                latest_price_date=latest,
                has_price_on_or_before_start=has_start,
                has_price_on_or_before_end=has_end,
            )
        )
    rows.sort(key=lambda r: (r.symbol_kind, r.symbol))
    return rows


def earliest_user_history_date(session: Session, user_id: str) -> datetime.date | None:
    candidates: list[datetime.date] = []

    txn_row = session.exec(
        select(InvestmentTransaction.txn_date)
        .join(Holding, InvestmentTransaction.holding_id == Holding.id)
        .where(Holding.user_id == user_id)
        .order_by(col(InvestmentTransaction.txn_date).asc())
        .limit(1)
    ).first()
    if isinstance(txn_row, datetime.date):
        candidates.append(txn_row)

    owned_orphan_platforms = [
        platform
        for platform in _PLATFORM_TO_MARKET_ASSET_CLASS
        if _platform_is_uniquely_owned_by_user(session, platform=platform, user_id=user_id)
    ]
    orphan_txn_row = None
    if owned_orphan_platforms:
        orphan_txn_row = session.exec(
            select(InvestmentTransaction.txn_date)
            .where(
                InvestmentTransaction.holding_id.is_(None),
                InvestmentTransaction.account_platform.in_(tuple(owned_orphan_platforms)),
            )
            .order_by(col(InvestmentTransaction.txn_date).asc())
            .limit(1)
        ).first()
    if isinstance(orphan_txn_row, datetime.date):
        candidates.append(orphan_txn_row)

    snap_row = session.exec(
        select(HoldingValueSnapshot.snapshot_date)
        .join(Holding, HoldingValueSnapshot.holding_id == Holding.id)
        .where(Holding.user_id == user_id)
        .order_by(col(HoldingValueSnapshot.snapshot_date).asc())
        .limit(1)
    ).first()
    if isinstance(snap_row, datetime.date):
        candidates.append(snap_row)

    holding_row = session.exec(
        select(Holding)
        .where(Holding.user_id == user_id, Holding.is_active == True)  # noqa: E712
        .order_by(col(Holding.created_at).asc())
        .limit(1)
    ).first()
    if holding_row is not None:
        created = holding_row.created_at
        if isinstance(created, datetime.datetime):
            candidates.append(created.date())
        elif isinstance(created, datetime.date):
            candidates.append(created)

    return min(candidates) if candidates else None
