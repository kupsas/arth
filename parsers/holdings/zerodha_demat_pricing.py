"""
Backfill ``price_per_unit`` / ``total_amount`` on Zerodha demat SOA legs (units-only PDF).

Equities: NSE bhav **close** from ``data/.nse_cache`` (``load_nse_equity_bhav_map_cached_first``).
Mutual funds: AMFI historical NAV (portal report, then mfapi.in fallback; one fetch per scheme per parse batch).
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field

from api.services.mf_nav_history import fetch_mf_nav_history
from api.services.price_feed import (
    _MIN_EQUITY_BHAV_SYMBOL_ROWS,
    canonical_nse_symbol,
    load_nse_equity_bhav_map_cached_first,
)
from parsers.holdings.base import ParsedInvestmentTxn
from parsers.holdings.security_kind import is_mf_investment_txn
from pipeline.isin_nse_resolver import lookup_isin_symbol
from pipeline.models import AssetClass

logger = logging.getLogger(__name__)

_KIND = "zerodha_demat_statement_pdf"
_MF_NAV_LOOKBACK_DAYS = 10
_NSE_SESSION_LOOKBACK_DAYS = 10
# MF demat credits (NSE/BSE Payout) are costed at the **previous business day's** NAV.
_MF_PAYOUT_DESC_RE = re.compile(
    r"(?i)(?:\b(?:nse|bse)\s+payout\b|\bpay[- ]?out\b)"
)


@dataclass
class _DematPriceContext:
    """Per-parse caches so one PDF does not re-read bhav / re-fetch MF history."""

    bhav_by_session: dict[datetime.date, dict[str, float] | None] = field(default_factory=dict)
    mf_nav_pairs: dict[str, list[tuple[datetime.date, float]]] = field(default_factory=dict)
    mf_fetch_attempted: set[str] = field(default_factory=set)


def _equity_symbol_for_txn(t: ParsedInvestmentTxn) -> str | None:
    sym = (t.symbol or "").strip()
    if sym and not sym.isdigit():
        return canonical_nse_symbol(sym)
    meta = t.metadata or {}
    isin = str(meta.get("isin") or "").strip().upper()
    if isin:
        nse = lookup_isin_symbol(isin)
        if nse:
            return canonical_nse_symbol(nse)
    return sym.upper() if sym else None


def _previous_weekday(d: datetime.date) -> datetime.date:
    x = d - datetime.timedelta(days=1)
    while x.weekday() >= 5:
        x -= datetime.timedelta(days=1)
    return x


def _mf_nav_as_of_date(t: ParsedInvestmentTxn) -> datetime.date:
    """NAV date for MF legs — payout lines use T-1 (previous business day)."""
    meta = t.metadata or {}
    desc = " ".join(
        str(x) for x in (meta.get("demat_description"), t.notes) if x and str(x).strip()
    )
    if _MF_PAYOUT_DESC_RE.search(desc):
        return _previous_weekday(t.txn_date)
    return t.txn_date


def _mf_scheme_code_for_txn(t: ParsedInvestmentTxn) -> str | None:
    meta = t.metadata or {}
    raw = meta.get("amfi_scheme_code") or t.symbol
    if raw and str(raw).strip().isdigit():
        return str(raw).strip()
    return None


def _load_bhav_map(ctx: _DematPriceContext, session: datetime.date) -> dict[str, float] | None:
    if session not in ctx.bhav_by_session:
        ctx.bhav_by_session[session] = load_nse_equity_bhav_map_cached_first(session)
    return ctx.bhav_by_session[session]


def _equity_close_on_date(
    symbol: str,
    preferred: datetime.date,
    ctx: _DematPriceContext,
) -> tuple[float | None, datetime.date | None]:
    """Latest NSE bhav close for ``symbol`` on or before ``preferred`` (weekday walk)."""
    sym = canonical_nse_symbol(symbol)
    d = preferred
    for _ in range(_NSE_SESSION_LOOKBACK_DAYS + 1):
        if d.weekday() < 5:
            bhav = _load_bhav_map(ctx, d)
            if bhav and len(bhav) >= _MIN_EQUITY_BHAV_SYMBOL_ROWS and sym in bhav:
                return float(bhav[sym]), d
        d -= datetime.timedelta(days=1)
    return None, None


def _ensure_mf_history(
    scheme_code: str,
    need_dates: list[datetime.date],
    ctx: _DematPriceContext,
) -> None:
    code = scheme_code.strip()
    if not code or code in ctx.mf_fetch_attempted:
        return
    ctx.mf_fetch_attempted.add(code)
    if not need_dates:
        return
    start = min(need_dates) - datetime.timedelta(days=_MF_NAV_LOOKBACK_DAYS)
    end = max(need_dates)
    rows = fetch_mf_nav_history(code, start, end)
    ctx.mf_nav_pairs[code] = sorted((r.date, float(r.close_price)) for r in rows)


def _mf_nav_on_date(
    scheme_code: str,
    preferred: datetime.date,
    ctx: _DematPriceContext,
) -> tuple[float | None, datetime.date | None]:
    pairs = ctx.mf_nav_pairs.get(scheme_code.strip(), [])
    if not pairs:
        return None, None
    by_date = {d: nav for d, nav in pairs}
    if preferred in by_date:
        return by_date[preferred], preferred
    d = preferred
    for _ in range(_MF_NAV_LOOKBACK_DAYS + 1):
        if d in by_date:
            return by_date[d], d
        d -= datetime.timedelta(days=1)
    return None, None


def _prefetch_mf_histories(txns: list[ParsedInvestmentTxn], ctx: _DematPriceContext) -> None:
    by_scheme: dict[str, list[datetime.date]] = {}
    for t in txns:
        if not is_mf_investment_txn(t):
            continue
        code = _mf_scheme_code_for_txn(t)
        if not code:
            continue
        by_scheme.setdefault(code, []).append(_mf_nav_as_of_date(t))
    for code, dates in by_scheme.items():
        _ensure_mf_history(code, dates, ctx)


def _apply_price_to_txn(
    t: ParsedInvestmentTxn,
    price: float,
    *,
    source: str,
    priced_on: datetime.date,
) -> ParsedInvestmentTxn:
    qty = float(t.quantity)
    total = round(qty * price, 2)
    meta = dict(t.metadata or {})
    meta["price_source"] = source
    meta["price_session_date"] = priced_on.isoformat()
    return t.model_copy(
        update={
            "price_per_unit": round(price, 6),
            "total_amount": total,
            "metadata": meta,
        }
    )


def apply_market_prices_to_zerodha_demat_txns(
    txns: list[ParsedInvestmentTxn],
    *,
    ctx: _DematPriceContext | None = None,
) -> list[ParsedInvestmentTxn]:
    """Fill zero-amount Zerodha demat legs from cached NSE bhav / AMFI NAV history."""
    if not txns:
        return txns
    work_ctx = ctx or _DematPriceContext()
    _prefetch_mf_histories(txns, work_ctx)

    out: list[ParsedInvestmentTxn] = []
    for t in txns:
        meta = t.metadata or {}
        if meta.get("kind") != _KIND:
            out.append(t)
            continue
        if t.total_amount > 0 and t.price_per_unit > 0:
            out.append(t)
            continue

        if is_mf_investment_txn(t):
            code = _mf_scheme_code_for_txn(t)
            if not code:
                logger.debug("Zerodha demat: no AMFI code for MF leg %s — skip price", t.name)
                out.append(t)
                continue
            nav_as_of = _mf_nav_as_of_date(t)
            nav, nav_date = _mf_nav_on_date(code, nav_as_of, work_ctx)
            if nav is None or nav_date is None:
                logger.info(
                    "Zerodha demat: no AMFI NAV for scheme %s on/ before %s",
                    code,
                    t.txn_date,
                )
                out.append(t)
                continue
            out.append(_apply_price_to_txn(t, nav, source="amfi_nav", priced_on=nav_date))
            continue

        if meta.get("asset_class") == AssetClass.EQUITY.value or not is_mf_investment_txn(t):
            sym = _equity_symbol_for_txn(t)
            if not sym:
                logger.debug("Zerodha demat: no NSE symbol for equity leg — skip price")
                out.append(t)
                continue
            close, session_d = _equity_close_on_date(sym, t.txn_date, work_ctx)
            if close is None or session_d is None:
                logger.info(
                    "Zerodha demat: no NSE bhav close for %s on/ before %s",
                    sym,
                    t.txn_date,
                )
                out.append(t)
                continue
            out.append(_apply_price_to_txn(t, close, source="nse_bhav", priced_on=session_d))
            continue

        out.append(t)
    return out
