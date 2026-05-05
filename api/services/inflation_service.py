"""
External inflation data + cache (Goals architecture Sub-Plan F).

Pulls **India** all-items CPI as a **monthly index** from the IMF SDMX API
(``IND.CPI._T.IX.M``), computes **year-on-year % change for every month** with
enough history, and stores **one ``InflationRate`` row per month** for
``CPI_GENERAL`` (period ``YYYY-MM``). No API key.

Sync runs:
  - On API startup (background thread, like price sync)
  - Weekly (scheduler, Asia/Kolkata)
  - On ``POST /api/inflation/refresh`` and when rate lookups trigger a refresh

Other categories stay ``INFLATION_DEFAULTS`` until another source exists.

Goal decomposition uses :func:`cpi_general_yoy_ema_pct` for **CPI_GENERAL**-mapped
subtypes — an **exponential** moving average of monthly YoY (``span`` months,
``α = 2/(span+1)``). Other subtypes use :data:`GOAL_INFLATION_MAP` →
:data:`INFLATION_DEFAULTS` / DB category rows (see :func:`resolve_goal_inflation`).

Attribution: IMF data — see https://data.imf.org/ and IMF terms of use.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any

import pandas as pd
from sqlmodel import Session, col, select

from api.models import Goal, InflationRate

logger = logging.getLogger(__name__)

# ── Defaults (annual %, nominal) — used when DB has no row for that category ──

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

# Short labels for API/UI — not the same as DB category keys
INFLATION_CATEGORY_LABELS: dict[str, str] = {
    "CPI_GENERAL": "India headline CPI (all items)",
    "REAL_ESTATE": "Housing & property costs",
    "EDUCATION": "Education costs",
    "HEALTHCARE": "Healthcare costs",
    "TRAVEL_INTERNATIONAL": "International travel",
    "TRAVEL_DOMESTIC": "Domestic travel",
}

CACHE_STALE_DAYS = 30
CACHE_EXPIRED_DAYS = 90

# IMF SDMX: monthly all-items CPI index for India (COICOP total _T).
IMF_DATAFLOW_CPI = "CPI"
IMF_INDIA_CPI_KEY = "IND.CPI._T.IX.M"

# Index history must start ≥12 months before the first stored YoY month. Default
# ``2018`` so YoY for ``2019-01`` can be computed; see ``MIN_STORED_CPI_PERIOD``.
DEFAULT_IMF_CPI_INDEX_START = "2018"
# Persist monthly YoY in ``inflation_rates`` only from this month onward (product default).
MIN_STORED_CPI_PERIOD = "2019-01"

# Goal decomposition: EMA span (months) for monthly YoY series — α = 2/(span+1).
# Override via ``INFLATION_SIMULATION_EMA_SPAN`` (``INFLATION_SIMULATION_MA_MONTHS`` still read for compatibility).
_DEFAULT_SIMULATION_EMA_SPAN = 84

# YoY % sanity bounds (IMF/IFS can spike on revisions)
_YOY_MIN = -10.0
_YOY_MAX = 60.0


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _imf_period_to_yyyy_mm(time_period: str) -> str:
    """``2025-M12`` → ``2025-12`` for storage/display."""
    if "-M" in time_period:
        y, rest = time_period.split("-M", 1)
        m = int(rest)
        return f"{y}-{m:02d}"
    return time_period[:7] if len(time_period) >= 7 else time_period


def _fetch_imf_cpi_index_series() -> pd.Series | None:
    """Download India monthly CPI index from IMF. Returns None on skip/failure."""
    if os.getenv("INFLATION_DISABLE_IMF", "").strip().lower() in ("1", "true", "yes"):
        logger.info("INFLATION_DISABLE_IMF set — skipping IMF CPI fetch.")
        return None

    try:
        import sdmx
    except ImportError as e:
        logger.warning("sdmx1 not installed (%s) — cannot fetch IMF CPI.", e)
        return None

    start = (os.getenv("IMF_CPI_START_PERIOD") or DEFAULT_IMF_CPI_INDEX_START).strip()

    try:
        IMF = sdmx.Client("IMF_DATA")
        msg = IMF.data(
            IMF_DATAFLOW_CPI,
            key=IMF_INDIA_CPI_KEY,
            params={"startPeriod": start},
        )
        ser = sdmx.to_pandas(msg)
    except Exception as e:
        logger.warning("IMF SDMX CPI request failed: %s", e)
        return None

    if not isinstance(ser, pd.Series) or ser.empty:
        logger.warning("IMF CPI returned empty series.")
        return None
    return ser


def monthly_yoy_pairs_from_imf_series(ser: pd.Series) -> list[tuple[str, float]]:
    """For each month with a full 12-month lookback, compute YoY %; period = ``YYYY-MM``."""
    df = ser.reset_index()
    if "TIME_PERIOD" not in df.columns:
        logger.warning("IMF CPI series missing TIME_PERIOD.")
        return []
    val_col = "value" if "value" in df.columns else ser.name
    if val_col not in df.columns:
        logger.warning("IMF CPI series has no value column.")
        return []

    df = df.sort_values("TIME_PERIOD").reset_index(drop=True)
    vals = pd.to_numeric(df[val_col], errors="coerce")
    out: list[tuple[str, float]] = []
    for i in range(12, len(df)):
        cur = vals.iloc[i]
        prev = vals.iloc[i - 12]
        if pd.isna(cur) or pd.isna(prev) or float(prev) <= 0:
            continue
        yoy = (float(cur) - float(prev)) / float(prev) * 100.0
        if yoy < _YOY_MIN or yoy > _YOY_MAX:
            continue
        p = _imf_period_to_yyyy_mm(str(df["TIME_PERIOD"].iloc[i]))
        out.append((p, round(float(yoy), 2)))
    return out


def _filter_pairs_minimum_calendar_month(
    pairs: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Keep only ``YYYY-MM`` >= :data:`MIN_STORED_CPI_PERIOD` (default: from 2019)."""
    return [(p, r) for p, r in pairs if p >= MIN_STORED_CPI_PERIOD]


def implied_imf_monthly_yoy_pairs() -> list[tuple[str, float]] | None:
    """Network: monthly YoY series for months >= ``MIN_STORED_CPI_PERIOD``, or None."""
    ser = _fetch_imf_cpi_index_series()
    if ser is None:
        return None
    raw = monthly_yoy_pairs_from_imf_series(ser)
    if not raw:
        logger.warning("IMF CPI: no monthly YoY pairs computed.")
        return None
    pairs = _filter_pairs_minimum_calendar_month(raw)
    if not pairs:
        logger.warning(
            "IMF CPI: no rows on/after %s — check IMF_CPI_START_PERIOD (need index data "
            "≥12 months before that month).",
            MIN_STORED_CPI_PERIOD,
        )
        return None
    return pairs


def sync_imf_cpi_history(session: Session) -> dict[str, Any]:
    """
    Replace all system ``CPI_GENERAL`` rows with the latest IMF monthly YoY history.

    One row per calendar month (``period`` = ``YYYY-MM``). All rows share the same
    ``fetched_at`` (this sync run). Returns a small summary dict.
    """
    pairs = implied_imf_monthly_yoy_pairs()
    if pairs is None:
        return {"ok": False, "months_written": 0, "reason": "fetch_failed_or_disabled"}
    if not pairs:
        # Do not wipe existing DB rows if the API returned an empty series.
        return {"ok": False, "months_written": 0, "reason": "no_monthly_pairs"}

    sync_time = _now_utc()
    # Remove previous IMF headline series so we don't duplicate periods.
    existing = session.exec(
        select(InflationRate).where(
            InflationRate.category == "CPI_GENERAL",
            InflationRate.user_id == "system",
        )
    ).all()
    for row in existing:
        session.delete(row)
    session.flush()

    for period, rate in pairs:
        session.add(
            InflationRate(
                category="CPI_GENERAL",
                rate=float(rate),
                source="IMF_SDMX",
                period=period,
                user_id="system",
                fetched_at=sync_time,
            )
        )
    try:
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning("Could not persist inflation history: %s", e)
        return {"ok": False, "months_written": 0, "error": str(e)}

    logger.info(
        "IMF CPI history synced — months=%d, latest_period=%s",
        len(pairs),
        pairs[-1][0],
    )
    return {
        "ok": True,
        "months_written": len(pairs),
        "latest_period": pairs[-1][0],
        "latest_yoy_pct": pairs[-1][1],
        "synced_at": sync_time.isoformat(),
    }


def fetch_cpi_from_imf_sdmx() -> dict[str, float | str] | None:
    """
    Latest headline YoY (convenience for callers that expect one dict).

    Does not write to the DB — use :func:`sync_imf_cpi_history` for persistence.
    """
    pairs = implied_imf_monthly_yoy_pairs()
    if not pairs:
        return None
    period, rate = pairs[-1]
    return {"CPI_GENERAL": rate, "_period": period}


def _latest_row_for_category(session: Session, category: str) -> InflationRate | None:
    q = select(InflationRate).where(
        InflationRate.category == category,
        InflationRate.user_id == "system",
    )
    # CPI_GENERAL: many rows (one per month) — use latest calendar month.
    if category == "CPI_GENERAL":
        q = q.order_by(col(InflationRate.period).desc())
    else:
        q = q.order_by(col(InflationRate.fetched_at).desc())
    return session.exec(q).first()


def _latest_sync_time_cpi_general(session: Session) -> datetime.datetime | None:
    """Most recent batch sync time (all CPI_GENERAL rows share it)."""
    row = session.exec(
        select(InflationRate)
        .where(
            InflationRate.category == "CPI_GENERAL",
            InflationRate.user_id == "system",
        )
        .order_by(col(InflationRate.fetched_at).desc())
    ).first()
    return row.fetched_at if row else None


def _age_days(ts: datetime.datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.UTC)
    return max(0, (_now_utc() - ts).days)


def fetch_and_cache_inflation(session: Session) -> dict[str, float]:
    """Run full IMF history sync, then merged headline map for all categories."""
    sync_imf_cpi_history(session)

    out: dict[str, float] = dict(INFLATION_DEFAULTS)
    for cat in INFLATION_DEFAULTS:
        row = _latest_row_for_category(session, cat)
        if row is not None:
            out[cat] = float(row.rate)
    return out


def _simulation_ema_span() -> int:
    """EMA ``span`` (1–120, default 12). Older env ``INFLATION_SIMULATION_MA_MONTHS`` still works."""
    for key in ("INFLATION_SIMULATION_EMA_SPAN", "INFLATION_SIMULATION_MA_MONTHS"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        try:
            return max(1, min(int(raw), 120))
        except ValueError:
            continue
    return _DEFAULT_SIMULATION_EMA_SPAN


def simulation_inflation_ema_span() -> int:
    """EMA window (months) — returned next to the blended % in decompose responses."""
    return _simulation_ema_span()


def simulation_inflation_trailing_months() -> int:
    """Backward-compatible alias — same numeric window as :func:`simulation_inflation_ema_span`."""
    return _simulation_ema_span()


def _yoy_ema_last(values_oldest_to_newest: list[float], span: int) -> float:
    """Recursive EMA (``adjust=False``): last value weights recent months more.

    ``α = 2/(span+1)``; seed with the oldest observation, then walk forward in time.
    """
    if not values_oldest_to_newest:
        raise ValueError("values_oldest_to_newest must be non-empty")
    if len(values_oldest_to_newest) == 1:
        return float(values_oldest_to_newest[0])
    span = max(1, span)
    alpha = 2.0 / (float(span) + 1.0)
    ema = float(values_oldest_to_newest[0])
    for x in values_oldest_to_newest[1:]:
        ema = alpha * float(x) + (1.0 - alpha) * ema
    return ema


def _ensure_fresh_cpi_general(session: Session) -> None:
    """Refresh IMF CPI rows if missing or cache older than :data:`CACHE_STALE_DAYS`."""
    sync_ts = _latest_sync_time_cpi_general(session)
    if sync_ts is None:
        fetch_and_cache_inflation(session)
        return
    if _age_days(sync_ts) < CACHE_STALE_DAYS:
        return
    fetch_and_cache_inflation(session)


def cpi_general_yoy_ema_pct(session: Session) -> float:
    """
    **Exponential** moving average of the last *N* stored monthly YoY % values for
    ``CPI_GENERAL`` (*N* = :func:`simulation_inflation_ema_span`, chronological EMA).

    Uses the same recursive rule as pandas ``ewm(span=N, adjust=False)``: recent
    months influence the result more than a simple mean. If there is no history,
    falls back to ``INFLATION_DEFAULTS['CPI_GENERAL']``.

    For goals mapped to ``CPI_GENERAL`` only; other subtypes use sector defaults
    or DB rows — see :func:`resolve_goal_inflation`.
    """
    _ensure_fresh_cpi_general(session)
    n = _simulation_ema_span()
    rows = session.exec(
        select(InflationRate)
        .where(
            InflationRate.category == "CPI_GENERAL",
            InflationRate.user_id == "system",
        )
        .order_by(col(InflationRate.period).desc())
        .limit(n)
    ).all()
    if not rows:
        return float(INFLATION_DEFAULTS["CPI_GENERAL"])
    # Chronological order (oldest → newest) for forward EMA.
    chronological = list(reversed(rows))
    vals = [float(r.rate) for r in chronological]
    return round(_yoy_ema_last(vals, n), 2)


def cpi_general_yoy_moving_average_pct(session: Session) -> float:
    """Deprecated name — use :func:`cpi_general_yoy_ema_pct`."""
    return cpi_general_yoy_ema_pct(session)


def get_inflation_rate(session: Session, category: str) -> float:
    """Latest annual % for *category* with stale/fetch logic."""
    cat = category.strip().upper()
    row = _latest_row_for_category(session, cat)

    if cat == "CPI_GENERAL":
        _ensure_fresh_cpi_general(session)
        row2 = _latest_row_for_category(session, cat)
        return (
            float(row2.rate)
            if row2 is not None
            else float(INFLATION_DEFAULTS["CPI_GENERAL"])
        )

    # Non-headline categories: single row or default
    if row is not None:
        age = _age_days(row.fetched_at)
        if age < CACHE_STALE_DAYS:
            return float(row.rate)
        if age < CACHE_EXPIRED_DAYS:
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


def _cpi_general_ema_or_default(session: Session) -> float:
    """EMA of IMF monthly YoY for ``CPI_GENERAL``; on failure, ``INFLATION_DEFAULTS['CPI_GENERAL']``."""
    try:
        return float(cpi_general_yoy_ema_pct(session))
    except Exception as e:
        logger.warning(
            "cpi_general_yoy_ema_pct failed (%s) — using default %s%%",
            e,
            INFLATION_DEFAULTS["CPI_GENERAL"],
        )
        return float(INFLATION_DEFAULTS["CPI_GENERAL"])


def resolve_goal_inflation(session: Session, goal: Goal) -> dict[str, Any]:
    """Resolve annual inflation % + metadata for simulation, UI, and decomposition.

    Priority:
      1. ``goal_specific_inflation_rate`` when set (user override).
      2. ``0`` for ``LOAN_PAYOFF`` (no price-level adjustment).
      3. ``CPI_GENERAL`` subtype bucket → :func:`_cpi_general_ema_or_default` (not a static 6%).
      4. Any other mapped category → :func:`get_inflation_rate` (DB row or
         :data:`INFLATION_DEFAULTS` e.g. REAL_ESTATE 8%%).
    """
    st = (goal.goal_subtype or "CUSTOM").strip().upper()
    mapped: str | None = GOAL_INFLATION_MAP.get(st, "CPI_GENERAL")

    if goal.goal_specific_inflation_rate is not None:
        pct = float(goal.goal_specific_inflation_rate)
        label = INFLATION_CATEGORY_LABELS.get(mapped or "CPI_GENERAL", "mapped category")
        return {
            "annual_pct": pct,
            "category": mapped,
            "method": "user_override",
            "label": label,
            "detail": "You set this goal’s inflation % manually.",
        }

    if mapped is None:
        return {
            "annual_pct": 0.0,
            "category": None,
            "method": "loan_zero",
            "label": "loan payoff",
            "detail": "No price-level adjustment (loan / fixed cash flows).",
        }

    if mapped == "CPI_GENERAL":
        pct = _cpi_general_ema_or_default(session)
        span = simulation_inflation_ema_span()
        return {
            "annual_pct": round(pct, 2),
            "category": "CPI_GENERAL",
            "method": "cpi_general_ema",
            "label": INFLATION_CATEGORY_LABELS["CPI_GENERAL"],
            "detail": (
                f"Headline CPI: exponential moving average of the last {span} "
                "monthly India YoY prints (IMF series)."
            ),
        }

    pct = float(get_inflation_rate(session, mapped))
    cat_label = INFLATION_CATEGORY_LABELS.get(mapped, mapped.replace("_", " ").lower())
    return {
        "annual_pct": round(pct, 2),
        "category": mapped,
        "method": "category_default",
        "label": cat_label,
        "detail": (
            f"Nominal {cat_label} inflation (cached series or default for this category — "
            "not the generic CPI EMA)."
        ),
    }


def get_goal_inflation_rate(session: Session, goal: Goal) -> float:
    """Annual inflation % for this goal — see :func:`resolve_goal_inflation`."""
    return float(resolve_goal_inflation(session, goal)["annual_pct"])


def merge_rates_from_db(session: Session) -> dict[str, float]:
    """Latest DB value per category, else INFLATION_DEFAULTS (no live fetch)."""
    out: dict[str, float] = dict(INFLATION_DEFAULTS)
    for cat in INFLATION_DEFAULTS:
        row = _latest_row_for_category(session, cat)
        if row is not None:
            out[cat] = float(row.rate)
    return out


def list_cpi_general_monthly_history_payload(
    session: Session,
    *,
    limit: int = 240,
) -> dict[str, Any]:
    """JSON-friendly history + sync metadata."""
    rows = session.exec(
        select(InflationRate)
        .where(
            InflationRate.category == "CPI_GENERAL",
            InflationRate.user_id == "system",
        )
        .order_by(col(InflationRate.period).desc())
        .limit(max(1, min(limit, 600)))
    ).all()
    sync_ts = None
    out_list: list[dict[str, Any]] = []
    for r in rows:
        if sync_ts is None:
            sync_ts = r.fetched_at.isoformat()
        out_list.append(
            {
                "period": r.period,
                "yoy_pct": float(r.rate),
                "source": r.source,
            }
        )
    return {
        "series": "IND_CPI_ALL_ITEMS_YOY_PCT",
        "synced_at": sync_ts,
        "count": len(out_list),
        "months": out_list,
    }


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
