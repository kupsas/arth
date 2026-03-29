"""
Live PPF annual rate for maturity illustrations.

We fetch the **current** rate sentence from the English Wikipedia article
``Public Provident_Fund_(India)`` via the official MediaWiki API (JSON, no
scraping of article HTML). That text is community-maintained but usually tracks
the notified rate; we still label it clearly and fall back to 7.1% if the
network or regex fails — **always verify** against the quarterly Ministry of
Finance / small-savings notification before acting on the number.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Last resort when Wikipedia is unreachable or the article layout changes.
DEFAULT_PPF_RATE_ANNUAL_PERCENT = 7.1

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_TITLE = "Public_Provident_Fund_(India)"
# Intro paragraph in the plain-text extract (Mar 2026: "The current interest rate is 7.1% annually …")
_CURRENT_RATE_RE = re.compile(
    r"The current interest rate is (\d+(?:\.\d+)?)% annually",
    re.IGNORECASE,
)

# (rate_percent, human_note, monotonic_timestamp)
_cache: tuple[float, str, float] | None = None
_CACHE_TTL_SEC = 6 * 3600

USER_AGENT = (
    "ArthPersonalFinance/1.0 (private portfolio tool; "
    "rate lookup for PPF maturity illustration only)"
)


def _parse_rate_from_extract(extract: str) -> float | None:
    m = _CURRENT_RATE_RE.search(extract)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _fetch_rate_from_wikipedia() -> tuple[float, str]:
    params: dict[str, Any] = {
        "action": "query",
        "format": "json",
        "titles": WIKI_TITLE,
        "prop": "extracts",
        "explaintext": True,
    }
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(WIKI_API, params=params, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning("PPF Wikipedia rate fetch failed: %s", exc)
        return DEFAULT_PPF_RATE_ANNUAL_PERCENT, (
            f"Fallback {DEFAULT_PPF_RATE_ANNUAL_PERCENT}% p.a. — could not reach Wikipedia; "
            "confirm the live rate from the GOI small-savings quarterly notification."
        )

    pages = data.get("query", {}).get("pages", {})
    extract = ""
    for _pid, page in pages.items():
        extract = page.get("extract") or ""
        break
    pct = _parse_rate_from_extract(extract)
    if pct is None:
        logger.warning("PPF rate regex miss on Wikipedia extract (article layout may have changed)")
        return DEFAULT_PPF_RATE_ANNUAL_PERCENT, (
            f"Fallback {DEFAULT_PPF_RATE_ANNUAL_PERCENT}% p.a. — could not parse Wikipedia; "
            "confirm from official GOI notification."
        )

    return pct, (
        "Live text from Wikipedia (Public Provident Fund (India)), phrase "
        "'The current interest rate is … % annually' via MediaWiki API — "
        "cross-check with the Ministry of Finance quarterly small-savings rates."
    )


def get_ppf_reference_rate_for_projection() -> tuple[float, str]:
    """Return (annual_percent, short provenance note). Cached several hours per process."""
    global _cache
    now = time.monotonic()
    if _cache is not None and (now - _cache[2]) < _CACHE_TTL_SEC:
        return _cache[0], _cache[1]
    pct, note = _fetch_rate_from_wikipedia()
    _cache = (pct, note, now)
    return pct, note


def clear_ppf_rate_cache_for_tests() -> None:
    global _cache
    _cache = None
