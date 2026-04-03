"""
Fill optional classification fields on ``Holding`` rows for the holdings UI (Phase B).

**Equity / ESOP**
- ``sector`` — NSE ``equityMetaInfo`` → ``industry`` (throttled ~3 req/s).
- ``market_cap_class`` — manual NSE-symbol map for now (AMFI cap list is often PDF;
  replace with a downloaded table when you automate it).

**Mutual funds**
- ``fund_category`` / ``fund_house`` — parsed from AMFI ``NAVAll.txt`` section headers
  (see :func:`api.services.price_feed.parse_amfi_navall`).

Call :func:`enrich_holdings` from the API or ``scripts/enrich_holdings.py``.
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlmodel import Session, col, select

from api.models import Holding
from api.services.price_feed import (
    AMFI_NAV_ALL_URL,
    canonical_nse_symbol,
    get_nse_client,
    parse_amfi_navall,
)
from pipeline.models import AssetClass

logger = logging.getLogger(__name__)

# Space NSE meta calls ~3/s (same order of magnitude as bhav throttling in price_feed).
_NSE_META_MIN_INTERVAL_SEC = 0.34

# ---------------------------------------------------------------------------
# Market cap — pragmatic manual seed keyed by canonical NSE symbol.
# SEBI large/mid/small definitions move over time; refresh when you add names.
# ---------------------------------------------------------------------------
_MANUAL_NSE_MARKET_CAP: dict[str, str] = {
    "APOLLOTYRE": "MID_CAP",
    "BEL": "LARGE_CAP",
    "BHEL": "MID_CAP",
    "HDFCBANK": "LARGE_CAP",
    "INDIGO": "LARGE_CAP",
    "IOC": "LARGE_CAP",
    "KANSAINER": "MID_CAP",
    "LT": "LARGE_CAP",
    "MINDACORP": "SMALL_CAP",
    "RELIANCE": "LARGE_CAP",
    "STOONE": "SMALL_CAP",
    "TATAPOWER": "LARGE_CAP",
    "ZENSARTECH": "SMALL_CAP",
}


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
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            r = client.get(AMFI_NAV_ALL_URL)
            r.raise_for_status()
            navall_text = r.text

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


def enrich_equities_from_nse(
    session: Session,
    *,
    user_id: str | None = None,
    report: EnrichmentReport | None = None,
) -> None:
    """Set ``sector`` via NSE and ``market_cap_class`` via manual map."""
    rep = report if report is not None else EnrichmentReport()
    q = select(Holding).where(
        Holding.is_active == True,  # noqa: E712
        col(Holding.asset_class).in_(
            (AssetClass.EQUITY.value, AssetClass.ESOP.value)
        ),
    )
    if user_id:
        q = q.where(Holding.user_id == user_id)
    holdings = list(session.exec(q).all())

    nse = get_nse_client()
    last_call = 0.0

    for h in holdings:
        sym_raw = (h.symbol or "").strip()
        if not sym_raw:
            continue
        # Skip Yahoo-style intl tickers — NSE meta will not apply.
        if "=" in sym_raw:
            continue

        nse_sym = canonical_nse_symbol(sym_raw)

        cap_changed = False
        cap = _MANUAL_NSE_MARKET_CAP.get(nse_sym)
        if cap is not None and h.market_cap_class != cap:
            h.market_cap_class = cap
            cap_changed = True
            rep.equities_cap_updated += 1
        elif cap is None:
            rep.equities_cap_unknown_symbol += 1

        # Throttle only around the live NSE call (same idea as bhav backfill spacing).
        elapsed = time.monotonic() - last_call
        if elapsed < _NSE_META_MIN_INTERVAL_SEC:
            time.sleep(_NSE_META_MIN_INTERVAL_SEC - elapsed)
        last_call = time.monotonic()

        sector_changed = False
        try:
            info = nse.equityMetaInfo(nse_sym)
            industry = (info or {}).get("industry")
            if isinstance(industry, str) and industry.strip():
                ind = industry.strip()
                if h.sector != ind:
                    h.sector = ind
                    sector_changed = True
                    rep.equities_sector_updated += 1
        except Exception as exc:
            logger.warning("NSE equityMetaInfo failed for %s: %s", nse_sym, exc)
            rep.equities_sector_failed += 1

        if cap_changed or sector_changed:
            _touch_holding(h)
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
