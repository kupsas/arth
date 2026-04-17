"""
Build and refresh :class:`api.models.NseEquityReference` from NSE **Nifty indices** + **CM bhav**.

**Universe:** NIFTY 100 ∪ NIFTY MIDCAP 150 ∪ **every symbol** in the latest bhav session.
The bhav file lists equities alongside NCDs, G-Secs, SGBs, T-bills, InvITs, REITs, SDLs, etc.

**``instrument_kind``** (coarse, from bhav ``SCTYSRS`` / series):
- ``RR`` → REIT, ``IV`` → INVIT, ``GB`` → SGB, ``GS`` → GSEC, ``TB`` → TBILL, ``SG`` → SDL
- ``N*`` debenture-style series (``N1``, ``NA``, ``NE``, …) → NCD
- ``Z`` + digit (e.g. ``Z8``) → NCD
- ``Y`` + digit (trust-style tranches) → DEBT_STRUCTURED
- ``E`` + digit (e.g. ``E1``) → DEBT_STRUCTURED
- ``EQ`` / ``BE`` / ``SM`` / ``ST`` / ``BZ`` / ``P1`` (and ``FININSTRMTP=STK``) → EQUITY
- anything else → UNKNOWN

**Market cap** (``LARGE_CAP`` / ``MID_CAP`` / ``SMALL_CAP``) is assigned **only** when
``instrument_kind=EQUITY``: index membership sets large/mid; other equities that appear
only in bhav are ``SMALL_CAP``. Non-equities get ``market_cap_class=NULL`` so downstream
enrichment never labels a bond as a small-cap stock.

Run :func:`refresh_nse_equity_reference` from ``scripts/refresh_nse_equity_reference.py``
after NSE connectivity is configured (same as price refresh).
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import time
from collections import Counter
from typing import Any

from sqlmodel import Session, delete

from api.models import NseEquityReference
from api.services.price_feed import (
    get_nse_client,
    latest_bhav_target_date,
    load_nse_equity_bhav_full_rows,
    resolve_nse_bhav_session_and_map,
)

logger = logging.getLogger(__name__)

_INDEX_THROTTLE_SEC = 0.5

# NSE CM bhav ``SctySrs`` values that behave like tradeable corporate equity / SME / T2T
# common stock rows (still require ``FININSTRMTP=STK`` in :func:`instrument_kind_from_bhav_row`).
_EQUITY_STYLE_SCTYSRS = frozenset({"EQ", "BE", "SM", "ST", "BZ"})

# Single-series overrides (checked before N-prefixed NCD heuristics — ``IV`` is InvIT, not NCD).
_SCTYSRS_INSTRUMENT_KIND: dict[str, str] = {
    "RR": "REIT",
    "IV": "INVIT",
    "GB": "SGB",
    "GS": "GSEC",
    "TB": "TBILL",
    "SG": "SDL",
}

_Y_SERIES_DEBT = re.compile(r"^Y\d$")
_E_SERIES_DEBT = re.compile(r"^E\d$")


def instrument_kind_from_bhav_row(row: dict[str, str] | None) -> str:
    """
    Map one bhav row to a coarse ``instrument_kind`` string stored on :class:`NseEquityReference`.

    NSE often uses ``FININSTRMTP=STK`` for both shares and many debt listings; **series**
    (``SCTYSRS``) is the reliable discriminator for EQ vs N1 vs GS, etc.
    """
    if not row:
        return "UNKNOWN"
    srs = (row.get("SCTYSRS") or row.get("SctySrs") or "").strip().upper()
    if not srs:
        return "UNKNOWN"

    mapped = _SCTYSRS_INSTRUMENT_KIND.get(srs)
    if mapped is not None:
        return mapped

    # Corporate NCD-style ``Z8`` etc. (letter Z + digit) — still debt, not equity ``BZ``.
    if len(srs) == 2 and srs[0] == "Z" and srs[1].isdigit():
        return "NCD"
    if _Y_SERIES_DEBT.match(srs):
        return "DEBT_STRUCTURED"
    if _E_SERIES_DEBT.match(srs):
        return "DEBT_STRUCTURED"
    if len(srs) >= 2 and srs[0] == "N":
        return "NCD"

    ftp = (row.get("FININSTRMTP") or row.get("FinInstrmTp") or "").strip().upper()
    if ftp and ftp != "STK":
        return "UNKNOWN"

    if srs in _EQUITY_STYLE_SCTYSRS or srs == "P1":
        return "EQUITY"

    return "UNKNOWN"


def bhav_row_is_listed_equity_like(row: dict[str, str] | None) -> bool:
    """
    True if a UDIFF bhav row should be treated as **equity-style** for cap / stock logic.

    Kept for callers that only need a boolean; equivalent to
    ``instrument_kind_from_bhav_row(row) == "EQUITY"``.
    """
    return instrument_kind_from_bhav_row(row) == "EQUITY"


def _index_stock_rows(raw_response: dict) -> list[dict]:
    """Constituent equity rows only (drop the index headline row and odd rows)."""
    data = raw_response.get("data") or []
    out: list[dict] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        m = r.get("meta")
        if isinstance(m, dict) and m.get("symbol") == r.get("symbol"):
            out.append(r)
    return out


def _parse_float_cell(val: Any) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _bhav_last_price(rec: dict[str, str]) -> float | None:
    for key in ("CLSPRIC", "CLOSE", "ClsPric"):
        if key in rec:
            return _parse_float_cell(rec.get(key))
    return None


def _payload(index_row: dict | None, bhav_row: dict[str, str] | None) -> str:
    merged = {"index_row": index_row, "bhav_row": bhav_row}
    return json.dumps(merged, default=str, ensure_ascii=True)


def refresh_nse_equity_reference(session: Session, *, commit: bool = True) -> dict[str, Any]:
    """
    Replace ``nse_equity_reference`` with a fresh snapshot (Nifty 100 + Midcap 150 + full bhav).

    Returns counts for logging / CLI output.
    """
    nse = get_nse_client()
    preferred = latest_bhav_target_date()
    session_d, price_map = resolve_nse_bhav_session_and_map(preferred)
    if price_map is None:
        msg = "No usable NSE equity bhav session — cannot refresh reference rows"
        logger.error(msg)
        raise RuntimeError(msg)

    bhav_rows = load_nse_equity_bhav_full_rows(session_d) or {}

    time.sleep(_INDEX_THROTTLE_SEC)
    n100_raw = nse.listEquityStocksByIndex("NIFTY 100")
    time.sleep(_INDEX_THROTTLE_SEC)
    m150_raw = nse.listEquityStocksByIndex("NIFTY MIDCAP 150")

    n100 = _index_stock_rows(n100_raw if isinstance(n100_raw, dict) else {})
    m150 = _index_stock_rows(m150_raw if isinstance(m150_raw, dict) else {})

    large_map: dict[str, dict] = {}
    for r in n100:
        sym = str(r.get("symbol") or "").strip().upper()
        if sym:
            large_map[sym] = r

    mid_map: dict[str, dict] = {}
    for r in m150:
        sym = str(r.get("symbol") or "").strip().upper()
        if sym:
            mid_map[sym] = r

    all_syms = set(large_map) | set(mid_map) | set(bhav_rows.keys())

    session.exec(delete(NseEquityReference))

    n_large = n_mid = n_small = 0
    kind_counts: Counter[str] = Counter()
    now = datetime.datetime.now(datetime.UTC)

    for sym in sorted(all_syms):
        idx_row: dict | None = None
        br = bhav_rows.get(sym)

        if sym in large_map:
            instrument_kind = "EQUITY"
            cap: str | None = "LARGE_CAP"
            idx_row = large_map[sym]
            n_large += 1
        elif sym in mid_map:
            instrument_kind = "EQUITY"
            cap = "MID_CAP"
            idx_row = mid_map[sym]
            n_mid += 1
        else:
            instrument_kind = instrument_kind_from_bhav_row(br) if br else "UNKNOWN"
            if instrument_kind == "EQUITY":
                cap = "SMALL_CAP"
                n_small += 1
            else:
                cap = None

        kind_counts[instrument_kind] += 1

        # Narrow index ``meta`` for mypy: ``.get("meta")`` can be absent or non-dict.
        _raw_meta = (idx_row or {}).get("meta")
        meta: dict[str, Any] = _raw_meta if isinstance(_raw_meta, dict) else {}
        company = meta.get("companyName") if isinstance(meta.get("companyName"), str) else None
        industry = meta.get("industry") if isinstance(meta.get("industry"), str) else None
        isin = meta.get("isin") if isinstance(meta.get("isin"), str) else None
        if not company and br:
            for key in ("FININSTRMNM", "FinInstrmNm"):
                v = br.get(key)
                if v and str(v).strip():
                    company = str(v).strip()[:512]
                    break

        last_px = _parse_float_cell((idx_row or {}).get("lastPrice"))
        if last_px is None and br:
            last_px = _bhav_last_price(br)
        ffmc = _parse_float_cell((idx_row or {}).get("ffmc"))

        ref = NseEquityReference(
            symbol=sym,
            market_cap_class=cap,
            instrument_kind=instrument_kind,
            company_name=company.strip()[:512] if company else None,
            industry=industry.strip()[:256] if industry else None,
            isin=isin.strip()[:16] if isin else None,
            last_price=last_px,
            ffmc=ffmc,
            reference_json=_payload(idx_row, br),
            updated_at=now,
        )
        session.add(ref)

    if commit:
        session.commit()
    else:
        session.flush()

    out = {
        "bhav_session_date": session_d.isoformat(),
        "symbols_total": len(all_syms),
        "large_cap": n_large,
        "mid_cap": n_mid,
        "small_cap": n_small,
        "bhav_rows_total": len(bhav_rows),
        "instrument_kind": dict(sorted(kind_counts.items(), key=lambda kv: kv[0])),
    }
    logger.info("nse_equity_reference refreshed: %s", out)
    return out
