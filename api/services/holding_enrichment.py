"""
Fill optional classification fields on ``Holding`` rows for the holdings UI (Phase B).

**Equity / ESOP / listed gold sleeve (NSE ticker)**
- ``sector`` — NSE ``equityMetaInfo`` → ``industry``, **or** ``"ETF"`` for symbols ending
  in ``ETF`` / ``BEES`` (throttled ~3 req/s).
- ``market_cap_class`` — :class:`api.models.NseEquityReference` when populated by
  ``refresh_nse_equity_reference``, else :mod:`pipeline.market_cap_data` / overrides JSON.

**Mutual funds**
- ``fund_category`` / ``fund_house`` — parsed from AMFI ``NAVAll.txt`` section headers
  (see :func:`api.services.price_feed.parse_amfi_navall`).

Call :func:`enrich_holdings` from the API or ``scripts/enrich_holdings.py`` for full-portfolio
backfill. New equity / listed-ETF rows also run :func:`enrich_single_equity_classification`
automatically when created from the ledger or CSV ingest.
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, col, select

from api.models import Holding, NseEquityReference
from pipeline.market_cap_data import market_cap_for_symbol
from api.services.price_feed import (
    canonical_nse_symbol,
    get_nse_client,
    parse_amfi_navall,
)
from pipeline.models import AssetClass, ValuationMethod

logger = logging.getLogger(__name__)

# Space NSE meta calls ~3/s (same order of magnitude as bhav throttling in price_feed).
_NSE_META_MIN_INTERVAL_SEC = 0.34

# Listed ETF / ETF-like NSE symbols — NSE ``industry`` is often wrong ("Mutual Fund Scheme").
# Override sector for UI grouping (not an exchange classification).
SECTOR_LABEL_ETF = "ETF"

def _normalize_amfi_category_label(raw: str | None) -> str | None:
    """Turn ``Open Ended Schemes(Foo Bar)`` into ``Foo Bar`` for cleaner UI."""
    if not raw:
        return None
    s = raw.strip()
    if "(" in s and ")" in s:
        inner = s[s.index("(") + 1 : s.rindex(")")].strip()
        if inner:
            return inner
    return s


def _is_amfi_scheme_code(symbol: str) -> bool:
    return bool(symbol) and symbol.strip().isdigit()


def is_listed_etf_nse_symbol(nse_sym: str) -> bool:
    """Heuristic: NSE-listed ETF / ETF-like names (Gold/Silver BeES, *IETF, etc.)."""
    s = (nse_sym or "").strip().upper()
    if len(s) < 3:
        return False
    if s.endswith("ETF"):
        return True
    if s.endswith("BEES"):
        return True
    return False


def _holding_eligible_for_equity_enrichment(h: Holding) -> bool:
    if not h.is_active:
        return False
    if h.valuation_method != ValuationMethod.MARKET_PRICE.value:
        return False
    ac = h.asset_class
    if ac not in (
        AssetClass.EQUITY.value,
        AssetClass.ESOP.value,
        AssetClass.GOLD.value,
    ):
        return False
    sym_raw = (h.symbol or "").strip()
    if not sym_raw or "=" in sym_raw:
        return False
    return True


def _touch_holding(h: Holding) -> None:
    h.updated_at = datetime.datetime.now(datetime.UTC)


@dataclass
class EnrichmentReport:
    """Counts for logging and API responses — see :func:`enrich_holdings`."""

    mutual_funds_updated: int = 0
    mutual_funds_skipped_no_meta: int = 0
    equities_sector_updated: int = 0
    equities_sector_failed: int = 0
    equities_cap_updated: int = 0
    equities_cap_unknown_symbol: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "mutual_funds_updated": self.mutual_funds_updated,
            "mutual_funds_skipped_no_meta": self.mutual_funds_skipped_no_meta,
            "equities_sector_updated": self.equities_sector_updated,
            "equities_sector_failed": self.equities_sector_failed,
            "equities_cap_updated": self.equities_cap_updated,
            "equities_cap_unknown_symbol": self.equities_cap_unknown_symbol,
        }


def enrich_mutual_funds_from_amfi(
    session: Session,
    *,
    user_id: str | None = None,
    navall_text: str | None = None,
    report: EnrichmentReport | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """Download (or use) NAVAll, parse AMC/category headers, update MF holdings.

    Returns the scheme-code → (category, house) map (useful for tests).
    """
    rep = report if report is not None else EnrichmentReport()
    if navall_text is None:
        from pipeline.amfi_isin_map import read_cached_navall

        navall_text = read_cached_navall()

    _navs, meta = parse_amfi_navall(navall_text)

    q = select(Holding).where(
        Holding.is_active == True,  # noqa: E712
        col(Holding.asset_class) == AssetClass.MUTUAL_FUND.value,
    )
    if user_id:
        q = q.where(Holding.user_id == user_id)
    holdings = list(session.exec(q).all())

    for h in holdings:
        sym = (h.symbol or "").strip()
        if not _is_amfi_scheme_code(sym):
            rep.mutual_funds_skipped_no_meta += 1
            continue
        pair = meta.get(sym)
        if not pair:
            rep.mutual_funds_skipped_no_meta += 1
            continue
        raw_cat, house = pair
        cat = _normalize_amfi_category_label(raw_cat)
        if h.fund_category != cat or h.fund_house != house:
            h.fund_category = cat
            h.fund_house = house
            _touch_holding(h)
            session.add(h)
            rep.mutual_funds_updated += 1

    return meta


def _apply_equity_sector_and_cap(
    session: Session,
    h: Holding,
    nse: Any,
    *,
    report: EnrichmentReport | None,
    throttle: bool,
    last_call_ref: list[float],
) -> None:
    """Set ``market_cap_class``, then ``sector`` (cached NSE ref, ETF label, or live meta)."""
    rep = report
    sym_raw = (h.symbol or "").strip()
    nse_sym = canonical_nse_symbol(sym_raw)

    ref = session.get(NseEquityReference, nse_sym)

    cap_changed = False
    cap = ref.market_cap_class if ref and ref.market_cap_class else None
    if cap is None:
        cap = market_cap_for_symbol(nse_sym)
    if cap is not None and h.market_cap_class != cap:
        h.market_cap_class = cap
        cap_changed = True
        if rep is not None:
            rep.equities_cap_updated += 1
    elif cap is None and rep is not None:
        rep.equities_cap_unknown_symbol += 1

    if throttle:
        elapsed = time.monotonic() - last_call_ref[0]
        if elapsed < _NSE_META_MIN_INTERVAL_SEC:
            time.sleep(_NSE_META_MIN_INTERVAL_SEC - elapsed)
        last_call_ref[0] = time.monotonic()

    sector_changed = False
    cached_industry = (
        ref.industry.strip()
        if ref and ref.industry and isinstance(ref.industry, str) and ref.industry.strip()
        else None
    )
    if is_listed_etf_nse_symbol(nse_sym):
        if h.sector != SECTOR_LABEL_ETF:
            h.sector = SECTOR_LABEL_ETF
            sector_changed = True
            if rep is not None:
                rep.equities_sector_updated += 1
    elif cached_industry:
        if h.sector != cached_industry:
            h.sector = cached_industry
            sector_changed = True
            if rep is not None:
                rep.equities_sector_updated += 1
    else:
        try:
            info = nse.equityMetaInfo(nse_sym)
            industry = (info or {}).get("industry")
            if isinstance(industry, str) and industry.strip():
                ind = industry.strip()
                if h.sector != ind:
                    h.sector = ind
                    sector_changed = True
                    if rep is not None:
                        rep.equities_sector_updated += 1
        except Exception as exc:
            logger.warning("NSE equityMetaInfo failed for %s: %s", nse_sym, exc)
            if rep is not None:
                rep.equities_sector_failed += 1

    if cap_changed or sector_changed:
        _touch_holding(h)


def backfill_etf_sector_labels(session: Session) -> int:
    """Set ``sector`` to :data:`SECTOR_LABEL_ETF` for eligible holdings with ETF-style symbols.

    Use after changing ETF rules or to fix rows created before automatic ETF labelling.
    Returns the number of holdings updated.
    """
    rows = list(
        session.exec(select(Holding).where(Holding.is_active == True)).all()  # noqa: E712
    )
    n = 0
    for h in rows:
        if not _holding_eligible_for_equity_enrichment(h):
            continue
        nse_sym = canonical_nse_symbol((h.symbol or "").strip())
        if not is_listed_etf_nse_symbol(nse_sym):
            continue
        if h.sector != SECTOR_LABEL_ETF:
            h.sector = SECTOR_LABEL_ETF
            _touch_holding(h)
            session.add(h)
            n += 1
    return n


def enrich_single_equity_classification(session: Session, h: Holding) -> None:
    """Classify one equity / ESOP / listed-gold-ETF holding (sector + cap bucket).

    Safe to call after auto-create or CSV ingest; no-op if the row is not eligible.
    """
    if not _holding_eligible_for_equity_enrichment(h):
        return
    nse = get_nse_client()
    last_ref = [0.0]
    _apply_equity_sector_and_cap(
        session, h, nse, report=None, throttle=True, last_call_ref=last_ref
    )
    session.add(h)


def enrich_equities_from_nse(
    session: Session,
    *,
    user_id: str | None = None,
    report: EnrichmentReport | None = None,
) -> None:
    """Set ``sector`` via NSE (or ETF label) and ``market_cap_class`` via :func:`market_cap_for_symbol`."""
    rep = report if report is not None else EnrichmentReport()
    q = select(Holding).where(
        Holding.is_active == True,  # noqa: E712
        Holding.valuation_method == ValuationMethod.MARKET_PRICE.value,
        col(Holding.asset_class).in_(
            (
                AssetClass.EQUITY.value,
                AssetClass.ESOP.value,
                AssetClass.GOLD.value,
            )
        ),
    )
    if user_id:
        q = q.where(Holding.user_id == user_id)
    holdings = [h for h in session.exec(q).all() if _holding_eligible_for_equity_enrichment(h)]

    nse = get_nse_client()
    last_ref = [0.0]

    for h in holdings:
        _apply_equity_sector_and_cap(
            session, h, nse, report=rep, throttle=True, last_call_ref=last_ref
        )
        session.add(h)


def enrich_holdings(
    session: Session,
    *,
    user_id: str | None = None,
    commit: bool = True,
    navall_text: str | None = None,
) -> EnrichmentReport:
    """Run MF + equity enrichment. Single AMFI download when ``navall_text`` is omitted."""
    report = EnrichmentReport()
    enrich_mutual_funds_from_amfi(
        session, user_id=user_id, navall_text=navall_text, report=report
    )
    enrich_equities_from_nse(session, user_id=user_id, report=report)
    if commit:
        session.commit()
    return report
