"""
Build and maintain a local ISIN → AMFI scheme map from cached ``NAVAll.txt``.

Files live under ``data/.amfi_cache`` (gitignored). Used by
:mod:`pipeline.isin_amfi_resolver` for Zerodha demat MF classification and shared
with :mod:`api.services.price_feed` for NAV lookups.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from pipeline.config import REPO_ROOT

logger = logging.getLogger(__name__)

# AMFI moved this file behind a 302 to portal.amfiindia.com — follow redirects.
AMFI_NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

DEFAULT_AMFI_CACHE_DIR = REPO_ROOT / "data" / ".amfi_cache"
DEFAULT_NAVALL_PATH = DEFAULT_AMFI_CACHE_DIR / "NAVAll.txt"
DEFAULT_ISIN_TO_SCHEME_PATH = DEFAULT_AMFI_CACHE_DIR / "isin_to_scheme.json"

_ISIN_TOKEN = re.compile(r"^IN[A-Z][A-Z0-9]{9}$")


def amfi_cache_dir() -> Path:
    raw = os.environ.get("ARTH_AMFI_CACHE_DIR", str(DEFAULT_AMFI_CACHE_DIR))
    return Path(raw).resolve()


def navall_path() -> Path:
    raw = os.environ.get("ARTH_AMFI_NAVALL_PATH", str(DEFAULT_NAVALL_PATH))
    return Path(raw).resolve()


def isin_to_scheme_path() -> Path:
    raw = os.environ.get("ARTH_AMFI_ISIN_MAP_PATH", str(DEFAULT_ISIN_TO_SCHEME_PATH))
    return Path(raw).resolve()


def _parse_amfi_nav_date(s: str) -> datetime.date | None:
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _try_parse_nav_row(parts: list[str]) -> tuple[str, str, float, datetime.date] | None:
    """Scheme code, scheme name, NAV, published date from one NAVAll data row."""
    if len(parts) < 6:
        return None
    code = parts[0].strip()
    if not code.isdigit():
        return None
    nav_s = parts[4].strip() if len(parts) > 4 else ""
    name = parts[3].strip() if len(parts) > 3 else ""
    d = _parse_amfi_nav_date(parts[5].strip() if len(parts) > 5 else "")
    if d is None and parts:
        d = _parse_amfi_nav_date(parts[-1].strip())
    if d is None:
        return None
    try:
        nav = float(nav_s)
    except ValueError:
        return None
    return code, name, nav, d


def _isin_tokens_in_row(parts: list[str]) -> list[str]:
    """Collect valid Indian ISIN tokens from growth / reinvest / name columns."""
    out: list[str] = []
    for cell in parts[1:4]:
        tok = (cell or "").strip().upper()
        if _ISIN_TOKEN.match(tok):
            out.append(tok)
    return out


def build_isin_to_scheme_map(navall_text: str) -> dict[str, dict[str, Any]]:
    """Index every ISIN in NAVAll growth/reinvest columns → scheme metadata."""
    out: dict[str, dict[str, Any]] = {}
    for raw in navall_text.splitlines():
        line = raw.strip()
        if not line or ";" not in line:
            continue
        if line.startswith("Scheme Code"):
            continue
        parts = [p.strip() for p in line.split(";")]
        parsed = _try_parse_nav_row(parts)
        if parsed is None:
            continue
        code, name, nav, nav_date = parsed
        entry = {
            "scheme_code": code,
            "scheme_name": name or None,
            "nav": nav,
            "nav_date": nav_date.isoformat(),
        }
        for iso in _isin_tokens_in_row(parts):
            prev = out.get(iso)
            if prev and prev.get("scheme_code") != code:
                logger.debug(
                    "AMFI ISIN %s remapped %s → %s",
                    iso,
                    prev.get("scheme_code"),
                    code,
                )
            out[iso] = dict(entry)
    return out


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".amfi_map_",
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


def save_isin_to_scheme_map(
    data: dict[str, dict[str, Any]],
    path: Path | None = None,
) -> None:
    _atomic_write_json(path or isin_to_scheme_path(), data)


def load_isin_to_scheme_map(path: Path | None = None) -> dict[str, dict[str, Any]]:
    p = path or isin_to_scheme_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read AMFI ISIN map %s: %s", p, e)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        iso = k.strip().upper()
        if not _ISIN_TOKEN.match(iso):
            continue
        code = str(v.get("scheme_code") or "").strip()
        if not code.isdigit():
            continue
        out[iso] = {
            "scheme_code": code,
            "scheme_name": v.get("scheme_name"),
            "nav": v.get("nav"),
            "nav_date": v.get("nav_date"),
        }
    return out


def download_navall(dest: Path | None = None) -> Path:
    """Fetch NAVAll.txt from AMFI and write to ``dest`` (atomic replace)."""
    target = dest or navall_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(AMFI_NAV_ALL_URL)
        r.raise_for_status()
        text = r.text
    fd, tmp = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=".navall_",
        suffix=".tmp",
    )
    try:
        os.write(fd, text.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    logger.info("Downloaded AMFI NAVAll → %s (%d bytes)", target, len(text))
    return target


def _navall_is_stale(path: Path, *, max_age_hours: float) -> bool:
    if not path.is_file():
        return True
    age_s = time.time() - path.stat().st_mtime
    return age_s > max_age_hours * 3600.0


def refresh_amfi_isin_cache(
    *,
    force: bool = False,
    max_age_hours: float = 24.0,
) -> dict[str, dict[str, Any]]:
    """Download NAVAll when missing/stale and rebuild ``isin_to_scheme.json``."""
    nav_path = navall_path()
    map_path = isin_to_scheme_path()
    if force or _navall_is_stale(nav_path, max_age_hours=max_age_hours):
        try:
            download_navall(nav_path)
        except Exception:
            logger.exception("AMFI NAVAll download failed")
            if not nav_path.is_file():
                return {}
    try:
        text = nav_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("Could not read %s: %s", nav_path, e)
        return {}
    data = build_isin_to_scheme_map(text)
    save_isin_to_scheme_map(data, map_path)
    logger.info("AMFI ISIN map: %d rows → %s", len(data), map_path)
    return data


def read_cached_navall(
    *,
    refresh_if_stale: bool = True,
    max_age_hours: float = 24.0,
) -> str:
    """Return NAVAll text from cache, refreshing when stale if requested."""
    nav_path = navall_path()
    if refresh_if_stale and _navall_is_stale(nav_path, max_age_hours=max_age_hours):
        refresh_amfi_isin_cache(max_age_hours=max_age_hours)
    if not nav_path.is_file():
        download_navall(nav_path)
    return nav_path.read_text(encoding="utf-8", errors="replace")
