"""
External inflation data + cache (Goals architecture Sub-Plan F).

Fetches CPI from data.gov.in when ``DATA_GOV_IN_API_KEY`` is set; stores rows in
``InflationRate``; falls back to cached values (<90 days) then hard-coded defaults.

Parsing is defensive: OGD JSON shapes vary by dataset revision. Unit tests mock HTTP.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
from typing import Any

import httpx
from sqlmodel import Session, col, select

from api.models import Goal, InflationRate

logger = logging.getLogger(__name__)

# ── Defaults (annual %, nominal) ────────────────────────────────────────────

INFLATION_DEFAULTS: dict[str, float] = {
    "CPI_GENERAL": 6.0,
    "REAL_ESTATE": 8.0,
    "EDUCATION": 10.0,
    "HEALTHCARE": 10.0,
    "TRAVEL_INTERNATIONAL": 8.0,
    "TRAVEL_DOMESTIC": 6.0,
}

GOAL_INFLATION_MAP: dict[str, str | None] = {
    "HOME_PURCHASE": "REAL_ESTATE",
    "VEHICLE": "CPI_GENERAL",
    "WEDDING": "CPI_GENERAL",
    "CHILD_EDUCATION": "EDUCATION",
    "RETIREMENT": "CPI_GENERAL",
    "TRAVEL": "TRAVEL_DOMESTIC",
    "EMERGENCY_FUND": "CPI_GENERAL",
    "LOAN_PAYOFF": None,
    "CUSTOM": "CPI_GENERAL",
}

CACHE_STALE_DAYS = 30
CACHE_EXPIRED_DAYS = 90

# MoSPI CPI datasets move between resource_ids — set DATA_GOV_IN_CPI_RESOURCE_ID in .env.
DATA_GOV_BASE = "https://api.data.gov.in/resource"


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _parse_float_loose(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    s = str(x).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _record_sort_key(rec: dict[str, Any]) -> tuple[int, int, int]:
    """Best-effort (year, month) for sorting records chronologically."""
    y = 0
    m = 0
    for ky in ("year", "Year", "financial_year", "Year__"):
        v = rec.get(ky)
        if v is not None:
            try:
                y = int(str(v).split("-")[0].strip()[:4])
            except ValueError:
                pass
            break
    for km in ("month", "Month", "month_name"):
        v = rec.get(km)
        if v is None:
            continue
        sv = str(v).strip().lower()
        month_map = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        for name, num in month_map.items():
            if name in sv:
                m = num
                break
        if m == 0:
            try:
                m = int(sv[:2]) if sv.isdigit() else int(float(sv))
            except ValueError:
                pass
        if m:
            break
    # year_month combined field e.g. 202401
    ym = rec.get("year_month") or rec.get("YearMonth") or rec.get("yearmonth")
    if ym is not None and y == 0:
        s = re.sub(r"\D", "", str(ym))
        if len(s) >= 6:
            try:
                y = int(s[:4])
                m = int(s[4:6])
            except ValueError:
                pass
    return (y, m, 0)


def _direct_inflation_from_records(records: list[dict[str, Any]]) -> float | None:
    """If records expose a YoY inflation %, use the latest non-null."""
    if not records:
        return None
    scored: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for r in records:
        scored.append((_record_sort_key(r), r))
    scored.sort(key=lambda x: x[0])
    for _k, r in reversed(scored):
        for key in r:
            lk = str(key).lower()
            if "inflation" in lk or "yoy" in lk or lk.endswith("_y_o_y"):
                val = _parse_float_loose(r[key])
                if val is not None and 0 <= val <= 50:
                    return val
    return None


def _yoy_from_index_series(records: list[dict[str, Any]]) -> float | None:
    """Compute YoY % from a general index column when ≥13 sorted points exist."""
    index_keys: list[str] = []
    for r in records:
        for k, v in r.items():
            lk = str(k).lower()
            if "index" in lk and "inflation" not in lk:
                if _parse_float_loose(v) is not None:
                    index_keys.append(k)
                break
        if index_keys:
            break
    if not index_keys:
        # any numeric column with 'general' or 'combined'
        for r in records:
            for k, v in r.items():
                lk = str(k).lower()
                if ("general" in lk or "combined" in lk) and _parse_float_loose(v) is not None:
                    index_keys.append(k)
                    break
            if index_keys:
                break
    if not index_keys:
        return None
    key = index_keys[0]
    scored: list[tuple[tuple[int, int, int], float]] = []
    for r in records:
        v = _parse_float_loose(r.get(key))
        if v is None or v <= 0:
            continue
        scored.append((_record_sort_key(r), v))
    scored.sort(key=lambda x: x[0])
    if len(scored) < 13:
        return None
    latest = scored[-1][1]
    year_ago = scored[-13][1]
    if year_ago <= 0:
        return None
    return (latest - year_ago) / year_ago * 100.0


def _period_label(records: list[dict[str, Any]]) -> str:
    if not records:
        return "unknown"
    best = max(records, key=lambda r: _record_sort_key(r))
    y, m, _ = _record_sort_key(best)
    if y and m:
        return f"{y:04d}-{m:02d}"
    return "latest"


def fetch_cpi_from_data_gov_in() -> dict[str, float] | None:
    """Return at least CPI_GENERAL annual % from data.gov.in, or None."""
    api_key = (os.getenv("DATA_GOV_IN_API_KEY") or "").strip()
    resource_id = (os.getenv("DATA_GOV_IN_CPI_RESOURCE_ID") or "").strip()

    if not api_key:
        logger.warning("DATA_GOV_IN_API_KEY not set — skipping live CPI fetch.")
        return None
    if not resource_id:
        logger.warning(
            "DATA_GOV_IN_CPI_RESOURCE_ID not set — skipping live CPI fetch "
            "(set both env vars from data.gov.in)."
        )
        return None

    url = f"{DATA_GOV_BASE}/{resource_id}"
    params = {
        "api-key": api_key,
        "format": "json",
        "limit": 500,
    }
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            payload = r.json()
    except Exception as e:
        logger.warning("data.gov.in CPI request failed: %s", e)
        return None

    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        logger.warning("data.gov.in CPI response had no records.")
        return None

    rate = _direct_inflation_from_records(records)
    if rate is None:
        rate = _yoy_from_index_series(records)
    if rate is None or rate < 0 or rate > 50:
        logger.warning("Could not derive a sane CPI YoY rate from records.")
        return None

    period = _period_label(records)
    # Extra key consumed by fetch_and_cache_inflation (not a rate category).
    return {"CPI_GENERAL": round(rate, 2), "_period": period}


def _latest_row_for_category(session: Session, category: str) -> InflationRate | None:
    q = (
        select(InflationRate)
        .where(InflationRate.category == category)
        .where(InflationRate.user_id == "system")
        .order_by(col(InflationRate.fetched_at).desc())
    )
    return session.exec(q).first()


def _age_days(ts: datetime.datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.UTC)
    return max(0, (_now_utc() - ts).days)


def fetch_and_cache_inflation(session: Session) -> dict[str, float]:
    """Fetch CPI when possible, append cache row, merge with defaults; never raises."""
    fetched = fetch_cpi_from_data_gov_in()
    if fetched and "CPI_GENERAL" in fetched:
        period = str(fetched.get("_period", "unknown"))
        cpi = fetched.get("CPI_GENERAL")
        if cpi is not None:
            row = InflationRate(
                category="CPI_GENERAL",
                rate=float(cpi),
                source="MOSPI_CPI",
                period=period,
                user_id="system",
                fetched_at=_now_utc(),
            )
            session.add(row)
            try:
                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning("Could not persist InflationRate: %s", e)

    out: dict[str, float] = dict(INFLATION_DEFAULTS)
    for cat in INFLATION_DEFAULTS:
        row = _latest_row_for_category(session, cat)
        if row is not None:
            out[cat] = float(row.rate)
    return out


def get_inflation_rate(session: Session, category: str) -> float:
    """Latest annual % for *category* with stale/fetch logic."""
    cat = category.strip().upper()
    row = _latest_row_for_category(session, cat)
    if row is not None:
        age = _age_days(row.fetched_at)
        if age < CACHE_STALE_DAYS:
            return float(row.rate)
        if age < CACHE_EXPIRED_DAYS:
            # Soft stale: try refresh but return cache if fetch fails
            fetch_and_cache_inflation(session)
            row2 = _latest_row_for_category(session, cat)
            if row2 is not None:
                return float(row2.rate)
            return float(row.rate)

    fetch_and_cache_inflation(session)
    row3 = _latest_row_for_category(session, cat)
    if row3 is not None:
        return float(row3.rate)
    return float(INFLATION_DEFAULTS.get(cat, INFLATION_DEFAULTS["CPI_GENERAL"]))


def get_goal_inflation_rate(session: Session, goal: Goal) -> float:
    """Resolve annual inflation % for simulation / decomposition for this goal."""
    if goal.goal_specific_inflation_rate is not None:
        return float(goal.goal_specific_inflation_rate)
    st = (goal.goal_subtype or "CUSTOM").strip().upper()
    mapped = GOAL_INFLATION_MAP.get(st, "CPI_GENERAL")
    if mapped is None:
        return 0.0
    return get_inflation_rate(session, mapped)


def merge_rates_from_db(session: Session) -> dict[str, float]:
    """Latest DB value per category, else INFLATION_DEFAULTS (no HTTP)."""
    out: dict[str, float] = dict(INFLATION_DEFAULTS)
    for cat in INFLATION_DEFAULTS:
        row = _latest_row_for_category(session, cat)
        if row is not None:
            out[cat] = float(row.rate)
    return out


def all_current_rates_with_meta(session: Session) -> dict[str, Any]:
    """For GET /api/inflation — rates + source + freshness (no live HTTP)."""
    merged = merge_rates_from_db(session)
    details: dict[str, Any] = {}
    for cat in INFLATION_DEFAULTS:
        row = _latest_row_for_category(session, cat)
        rate = merged.get(cat, INFLATION_DEFAULTS[cat])
        if row is not None:
            details[cat] = {
                "rate": rate,
                "source": row.source,
                "period": row.period,
                "fetched_at": row.fetched_at.isoformat(),
                "age_days": _age_days(row.fetched_at),
            }
        else:
            details[cat] = {
                "rate": rate,
                "source": "FALLBACK_DEFAULT",
                "period": "n/a",
                "fetched_at": None,
                "age_days": None,
            }
    return {"rates": details, "headline": merged}
