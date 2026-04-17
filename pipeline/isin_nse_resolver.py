"""
Resolve Indian equity ISIN → NSE trading symbol using official bhavcopy data.

Static maps live in :mod:`pipeline.holding_parsers.icici_direct_equity` and optional
``data/icici_nse_symbol_overrides.json``. When an ISIN is missing there, we look it up
in the latest usable NSE equity bhav file (ISIN + TckrSymb columns), which covers the
full listed universe for that session.

Cached per process by session date to avoid repeated downloads.
"""

from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

_bhav_isin_cache_date: datetime.date | None = None
_bhav_isin_map: dict[str, str] | None = None


def invalidate_bhav_isin_cache() -> None:
    global _bhav_isin_cache_date, _bhav_isin_map
    _bhav_isin_cache_date = None
    _bhav_isin_map = None


def _merged_bhav_isin_map() -> dict[str, str]:
    """Latest session’s ISIN→symbol map (empty dict if unavailable)."""
    global _bhav_isin_cache_date, _bhav_isin_map
    from api.services.price_feed import (
        load_nse_equity_bhav_isin_map,
        latest_bhav_target_date,
        resolve_nse_bhav_session_and_map,
    )

    preferred = latest_bhav_target_date()
    session_d, price_map = resolve_nse_bhav_session_and_map(preferred)
    if price_map is None:
        return {}
    if _bhav_isin_map is not None and _bhav_isin_cache_date == session_d:
        return _bhav_isin_map

    m = load_nse_equity_bhav_isin_map(session_d)
    _bhav_isin_map = m if m is not None else {}
    _bhav_isin_cache_date = session_d
    if not _bhav_isin_map:
        logger.debug("NSE bhav ISIN map empty for session %s", session_d)
    return _bhav_isin_map


def lookup_isin_from_nse_bhav(isin: str) -> str | None:
    """
    Return NSE symbol for ``isin`` using the cached bhav map, or ``None``.

    Only accepts normal Indian ISINs (``IN`` + 10 chars = 12 total).
    """
    raw = (isin or "").strip().upper()
    if len(raw) != 12 or not raw.startswith("IN"):
        return None
    sym = _merged_bhav_isin_map().get(raw)
    return sym if sym else None
