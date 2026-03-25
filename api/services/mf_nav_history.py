"""
Historical mutual-fund NAV for backfills.

**Primary source — AMFI portal** (official): periodic text report for a date range
(``DownloadNAVHistoryReport_Po.aspx``).  Same scheme codes as ``NAVAll.txt``.

**Fallback — mfapi.in** (third-party JSON): used only if the portal returns nothing,
for resilience when the portal is slow or blocked.

Daily marks still use :func:`api.services.price_feed.fetch_mf_navs` (``NAVAll.txt`` snapshot).
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from api.models import Price

logger = logging.getLogger(__name__)

# Official AMFI: full NAV history for *all* schemes in range (large file per request).
AMFI_NAV_HISTORY_DOWNLOAD_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"

# Third-party mirror (unreliable); kept as fallback only.
MFAPI_SCHEME_HISTORY_URL = "https://api.mfapi.in/mf/{scheme_code}"

# Portal requests are heavy (~few MB each); keep chunks small and pause between them.
_DEFAULT_AMFI_CHUNK_DAYS = 21
_AMFI_CHUNK_PAUSE_SEC = 0.75
_AMFI_HTTP_TIMEOUT_SEC = 180.0


def _parse_mfapi_date(s: str) -> datetime.date | None:
    """mfapi.in uses ``DD-MM-YYYY`` on ``data`` rows."""
    s = s.strip()
    for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _fmt_amfi_portal_date(d: datetime.date) -> str:
    """``DD-MMM-YYYY`` as used by AMFI query params (e.g. ``01-Mar-2025``)."""
    return d.strftime("%d-%b-%Y")


def _parse_amfi_history_date(s: str) -> datetime.date | None:
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_amfi_nav_history_report_line(
    line: str,
    want_codes: frozenset[str],
) -> Price | None:
    """Parse one semicolon row from AMFI ``NAVHistoryReport`` into a :class:`Price` or ``None``."""
    raw = line.strip()
    if not raw or raw.startswith("Open Ended") or raw.startswith("Close Ended"):
        return None
    parts = [p.strip() for p in raw.split(";")]
    if len(parts) < 8:
        return None
    code = parts[0]
    if not code.isdigit() or code not in want_codes:
        return None
    date_s = parts[-1] or parts[-2] if len(parts) > 1 else ""
    if not date_s:
        return None
    d = _parse_amfi_history_date(date_s)
    if d is None:
        return None
    try:
        nav = float(parts[4].replace(",", ""))
    except (ValueError, IndexError):
        return None
    return Price(symbol=code, date=d, close_price=nav, source="amfi_portal")


def parse_amfi_nav_history_report(
    text: str,
    want_codes: frozenset[str],
    start: datetime.date,
    end: datetime.date,
) -> list[Price]:
    """Scan a full report body; keep rows for ``want_codes`` in ``[start, end]`` inclusive."""
    rows: list[Price] = []
    for line in text.splitlines():
        p = parse_amfi_nav_history_report_line(line, want_codes)
        if p is None:
            continue
        if p.date < start or p.date > end:
            continue
        rows.append(p)
    return rows


def fetch_mf_nav_histories_amfi_portal(
    scheme_codes: list[str],
    start: datetime.date,
    end: datetime.date,
    *,
    chunk_days: int = _DEFAULT_AMFI_CHUNK_DAYS,
    client: httpx.Client | None = None,
) -> list[Price]:
    """Download official AMFI NAV history for ``scheme_codes`` between ``start`` and ``end``.

    Each HTTP GET returns **all** schemes for that date window (large payload), so we chunk
    calendar ranges and filter to the codes you care about.
    """
    codes = sorted({c.strip() for c in scheme_codes if c.strip()})
    if not codes or start > end:
        return []
    want = frozenset(codes)
    collected: list[Price] = []

    def _one_chunk(c: httpx.Client, frm: datetime.date, to: datetime.date) -> None:
        q = urlencode({"frmdt": _fmt_amfi_portal_date(frm), "todt": _fmt_amfi_portal_date(to)})
        url = f"{AMFI_NAV_HISTORY_DOWNLOAD_URL}?{q}"
        try:
            r = c.get(url)
            r.raise_for_status()
        except Exception as exc:
            logger.warning("AMFI NAV history download failed %s → %s: %s", frm, to, exc)
            return
        rows = parse_amfi_nav_history_report(r.text, want, start, end)
        collected.extend(rows)
        logger.info(
            "AMFI NAV history chunk %s → %s: matched %d row(s) for %d scheme(s)",
            frm,
            to,
            len(rows),
            len(want),
        )

    close_client = False
    if client is None:
        client = httpx.Client(timeout=_AMFI_HTTP_TIMEOUT_SEC, follow_redirects=True)
        close_client = True
    try:
        chunk_start = start
        first = True
        while chunk_start <= end:
            span = datetime.timedelta(days=max(1, chunk_days) - 1)
            chunk_end = min(chunk_start + span, end)
            if not first:
                time.sleep(_AMFI_CHUNK_PAUSE_SEC)
            first = False
            _one_chunk(client, chunk_start, chunk_end)
            chunk_start = chunk_end + datetime.timedelta(days=1)
    finally:
        if close_client:
            client.close()

    return collected


def parse_mfapi_history_payload(payload: dict[str, Any]) -> list[tuple[datetime.date, float]]:
    """Turn raw mfapi.in JSON ``data`` into (date, nav) pairs (newest-first order preserved)."""
    raw_rows = payload.get("data")
    if not isinstance(raw_rows, list):
        return []
    out: list[tuple[datetime.date, float]] = []
    for item in raw_rows:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        d = _parse_mfapi_date(str(item[0]))
        if d is None:
            continue
        try:
            nav = float(str(item[1]).replace(",", ""))
        except ValueError:
            continue
        out.append((d, nav))
    return out


def mfapi_nav_to_prices_in_range(
    scheme_code: str,
    pairs: list[tuple[datetime.date, float]],
    start: datetime.date,
    end: datetime.date,
) -> list[Price]:
    """Keep NAV rows whose dates fall in ``[start, end]`` (inclusive)."""
    rows: list[Price] = []
    for d, nav in pairs:
        if d < start or d > end:
            continue
        rows.append(
            Price(
                symbol=scheme_code.strip(),
                date=d,
                close_price=nav,
                source="mfapi",
            )
        )
    return rows


def fetch_mf_nav_history_mfapi(
    scheme_code: str,
    start: datetime.date,
    end: datetime.date,
    *,
    client: httpx.Client | None = None,
) -> list[Price]:
    """Download scheme history from mfapi.in (fallback only)."""
    code = scheme_code.strip()
    url = MFAPI_SCHEME_HISTORY_URL.format(scheme_code=code)

    def _fetch_with(c: httpx.Client) -> list[Price]:
        try:
            r = c.get(url)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            logger.warning("mfapi.in history failed for scheme %s: %s", code, exc)
            return []
        pairs = parse_mfapi_history_payload(payload if isinstance(payload, dict) else {})
        return mfapi_nav_to_prices_in_range(code, pairs, start, end)

    if client is not None:
        return _fetch_with(client)
    with httpx.Client(timeout=60.0, follow_redirects=True) as c:
        return _fetch_with(c)


def fetch_mf_nav_history(
    scheme_code: str,
    start: datetime.date,
    end: datetime.date,
    *,
    client: httpx.Client | None = None,
) -> list[Price]:
    """Historical NAV for one scheme: try AMFI portal (shared report), then mfapi.in."""
    code = scheme_code.strip()
    amfi_rows = [r for r in fetch_mf_nav_histories_amfi_portal([code], start, end, client=client) if r.symbol == code]
    if amfi_rows:
        return amfi_rows
    return fetch_mf_nav_history_mfapi(code, start, end, client=client)
