"""
**What this is:** a small helper library — **not** a background service and **not**
something that scans your whole portfolio on a schedule.

It answers: “Given one or more **candidate** ticker strings (e.g. from a PDF, an LLM,
or a broker code), which is the first that **provably** exists on NSE?”

**Validation** (machine checks only — no trust without proof):

- ``ticker_has_nse_bhav_close`` — symbol appears in the latest resolved equity bhavcopy.
- ``ticker_has_nse_equity_meta`` — NSE ``equityMetaInfo`` returns metadata.
- ``ticker_valid_for_nse_equity`` — either of the above.

**Design:** Any automated resolver (including a future LLM step) must only **commit**
a symbol after at least one of these checks passes — never raw model output alone.

Typical flow for a list of candidate tickers (e.g. LLM outputs, ordered by preference)::

    for cand in candidates[:5]:
        if ticker_valid_for_nse_equity(cand):
            return canonical_nse_symbol(cand)
    return None  # fall back to data/icici_nse_symbol_overrides.json by hand

Portfolio **sector / cap** for holdings you already own is handled by
:mod:`api.services.holding_enrichment`, not this module.
"""

from __future__ import annotations

import datetime
import logging
from typing import Iterable

from api.services.price_feed import (
    canonical_nse_symbol,
    get_nse_client,
    latest_bhav_target_date,
    resolve_nse_bhav_session_and_map,
)

logger = logging.getLogger(__name__)


def ticker_has_nse_bhav_close(
    raw: str,
    *,
    trade_date: datetime.date | None = None,
) -> bool:
    """True if ``raw`` maps to a row in the latest available equity bhavcopy."""
    sym = canonical_nse_symbol(raw.strip())
    if not sym:
        return False
    preferred = latest_bhav_target_date() if trade_date is None else trade_date
    _session_d, m = resolve_nse_bhav_session_and_map(preferred)
    if not m or sym not in m:
        return False
    return m[sym] > 0


def ticker_has_nse_equity_meta(raw: str) -> bool:
    """True if NSE ``equityMetaInfo`` returns metadata for this symbol (industry, etc.)."""
    sym = canonical_nse_symbol(raw.strip())
    if not sym:
        return False
    try:
        nse = get_nse_client()
        info = nse.equityMetaInfo(sym)
    except Exception as exc:
        logger.debug("equityMetaInfo(%s) failed: %s", sym, exc)
        return False
    return bool(info)


def ticker_valid_for_nse_equity(raw: str) -> bool:
    """Bhav close **or** equity meta — enough to treat ``raw`` as a real NSE listing."""
    return ticker_has_nse_bhav_close(raw) or ticker_has_nse_equity_meta(raw)


def first_valid_ticker(candidates: Iterable[str]) -> str | None:
    """Return the first candidate that passes :func:`ticker_valid_for_nse_equity`, else ``None``."""
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        if ticker_valid_for_nse_equity(c):
            return canonical_nse_symbol(c)
    return None
