"""
NSE symbol → LARGE_CAP / MID_CAP / SMALL_CAP for holdings UI.

Defaults ship in code; optional JSON at ``data/nse_market_cap_overrides.json`` (gitignored)
merges on top so you can extend caps without editing Python.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pipeline.config import REPO_ROOT

logger = logging.getLogger(__name__)

DEFAULT_NSE_MARKET_CAP: dict[str, str] = {
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
    "BHARTIARTL": "LARGE_CAP",
    "TATASTEEL": "LARGE_CAP",
    "GOLDIETF": "LARGE_CAP",
    "SILVERIETF": "LARGE_CAP",
}

DEFAULT_OVERRIDES_PATH = REPO_ROOT / "data" / "nse_market_cap_overrides.json"

_cache_mtime: float | None = None
_cache_merged: dict[str, str] | None = None


def overrides_path() -> Path:
    return Path(
        os.environ.get("ARTH_NSE_MARKET_CAP_OVERRIDES", str(DEFAULT_OVERRIDES_PATH))
    ).resolve()


def _load_overrides_file() -> dict[str, str]:
    path = overrides_path()
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s: %s — using code defaults only", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        ku = k.strip().upper()
        vu = v.strip().upper()
        if ku and vu in ("LARGE_CAP", "MID_CAP", "SMALL_CAP"):
            out[ku] = vu
    return out


def merged_market_cap_map() -> dict[str, str]:
    """Code defaults merged with optional JSON overrides (override wins on duplicate keys)."""
    global _cache_mtime, _cache_merged
    path = overrides_path()
    mtime = path.stat().st_mtime if path.is_file() else None
    if _cache_merged is not None and mtime == _cache_mtime:
        return _cache_merged
    extra = _load_overrides_file()
    merged = {**DEFAULT_NSE_MARKET_CAP, **extra}
    _cache_mtime = mtime
    _cache_merged = merged
    return merged


def invalidate_market_cap_cache() -> None:
    global _cache_mtime, _cache_merged
    _cache_mtime = None
    _cache_merged = None


def market_cap_for_symbol(nse_sym: str) -> str | None:
    """Return cap bucket for canonical NSE symbol, or ``None`` if unknown."""
    return merged_market_cap_map().get((nse_sym or "").strip().upper())
