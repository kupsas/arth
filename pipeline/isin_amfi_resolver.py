"""
Resolve Indian MF demat ISIN → AMFI scheme code using cached NAVAll index.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pipeline.amfi_isin_map import load_isin_to_scheme_map, refresh_amfi_isin_cache

logger = logging.getLogger(__name__)

_map_cache: dict[str, dict[str, Any]] | None = None
_refresh_attempted = False

_NAME_TOKEN = re.compile(r"[A-Z0-9]+")


def invalidate_amfi_isin_cache() -> None:
    global _map_cache, _refresh_attempted
    _map_cache = None
    _refresh_attempted = False


def _load_map(*, try_refresh: bool = True) -> dict[str, dict[str, Any]]:
    global _map_cache, _refresh_attempted
    if _map_cache is not None:
        return _map_cache
    _map_cache = load_isin_to_scheme_map()
    if not _map_cache and try_refresh and not _refresh_attempted:
        _refresh_attempted = True
        try:
            _map_cache = refresh_amfi_isin_cache()
        except Exception:
            logger.exception("AMFI ISIN cache refresh failed during lookup")
            _map_cache = load_isin_to_scheme_map()
    return _map_cache or {}


def _name_token_bag(label: str | None) -> frozenset[str]:
    if not label:
        return frozenset()
    return frozenset(_NAME_TOKEN.findall(label.upper()))


def lookup_amfi_scheme_by_isin(
    isin: str,
    *,
    name_hint: str | None = None,
) -> dict[str, Any] | None:
    """Return ``{scheme_code, scheme_name, nav, nav_date}`` for ``isin``, or ``None``."""
    iso = (isin or "").strip().upper()
    if len(iso) != 12 or not iso.startswith("IN"):
        return None
    entry = _load_map().get(iso)
    if entry is None:
        return None
    if name_hint:
        hint_bag = _name_token_bag(name_hint)
        scheme_name = str(entry.get("scheme_name") or "")
        scheme_bag = _name_token_bag(scheme_name)
        if hint_bag and scheme_bag and len(hint_bag & scheme_bag) < 2:
            logger.debug(
                "AMFI ISIN %s name hint weak match (%r vs %r)",
                iso,
                name_hint,
                scheme_name,
            )
    return dict(entry)
