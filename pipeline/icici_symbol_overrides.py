"""
Optional ICICI ↔ NSE symbol overrides (manual JSON).

- **NSE bhavcopy** (see :func:`pipeline.isin_nse_resolver.lookup_isin_from_nse_bhav`) is
  the primary ISIN → symbol source for new imports.
- **Overrides** in ``data/icici_nse_symbol_overrides.json`` (gitignored — copy from
  ``*.example.json``) pin exceptions: ISINs missing from bhav (delisted), or ICICI short
  codes not in :data:`pipeline.holding_parsers.icici_direct_equity.ICICI_SHORT_TO_NSE`.

NSE *Trades executed* PDFs already carry the **NSE ``TckrSymb``** (same string bhavcopy uses).
If a PDF ever shows an alias, add a row under ``icici_short_to_nse`` so
:func:`api.services.price_feed.canonical_nse_symbol` resolves before refresh.
Validated multi-candidate flows can use :mod:`api.services.nse_ticker_resolve`.

Merged into :func:`api.services.price_feed.canonical_nse_symbol` and equity resolution.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pipeline.config import REPO_ROOT

logger = logging.getLogger(__name__)

DEFAULT_OVERRIDES_PATH = REPO_ROOT / "data" / "icici_nse_symbol_overrides.json"

# Env override for tests: ``ARTH_ICICI_SYMBOL_OVERRIDES=/path/to.json``
_cache_mtime: float | None = None
_cache_data: dict[str, Any] | None = None


def overrides_path() -> Path:
    """Resolved path to overrides JSON (reads ``ARTH_ICICI_SYMBOL_OVERRIDES`` each call)."""
    return Path(
        os.environ.get("ARTH_ICICI_SYMBOL_OVERRIDES", str(DEFAULT_OVERRIDES_PATH))
    ).resolve()


def invalidate_overrides_cache() -> None:
    global _cache_mtime, _cache_data
    _cache_mtime = None
    _cache_data = None


def load_overrides() -> dict[str, Any]:
    """Load JSON overrides; cache invalidates when file mtime changes."""
    global _cache_mtime, _cache_data
    path = overrides_path()
    if not path.is_file():
        return {"icici_short_to_nse": {}, "isin_to_nse": {}}
    mtime = path.stat().st_mtime
    if _cache_data is not None and _cache_mtime == mtime:
        return _cache_data
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s: %s — using empty overrides", path, e)
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("icici_short_to_nse", {})
    data.setdefault("isin_to_nse", {})
    if not isinstance(data["icici_short_to_nse"], dict):
        data["icici_short_to_nse"] = {}
    if not isinstance(data["isin_to_nse"], dict):
        data["isin_to_nse"] = {}
    _cache_mtime = mtime
    _cache_data = data
    return data


def save_overrides(data: dict[str, Any]) -> None:
    """Atomically write overrides JSON and invalidate the read cache."""
    path = overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "icici_short_to_nse": dict(data.get("icici_short_to_nse", {})),
        "isin_to_nse": dict(data.get("isin_to_nse", {})),
    }
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".icici_sym_",
        suffix=".tmp",
    )
    try:
        os.write(fd, text.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    invalidate_overrides_cache()


def merge_with_disk(base: dict[str, str], key: str) -> dict[str, str]:
    """Merge static ``base`` with on-disk overrides (file entries win on duplicate keys)."""
    data = load_overrides()
    extra = data.get(key, {})
    if not isinstance(extra, dict):
        extra = {}
    merged = {**base, **{k.upper(): str(v).upper() for k, v in extra.items()}}
    return merged
