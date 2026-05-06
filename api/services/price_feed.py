"""
Price ingestion for Layer 1 holdings (Phase A.1.2).

**Indian listed stocks, ESOP (NSE-listed), SGB, gold ETFs** — All use **NSE equity
bhavcopy** via the ``nse`` package: one file per session with official **raw**
closes (same convention as broker statements). There is **no Yahoo fallback** for
these; if bhav is missing (holiday, outage), we log and skip until the next run.

**Open-ended mutual funds** — **AMFI** daily ``NAVAll.txt`` (not NSE bhav, not Yahoo).
NSE lists some MF *ETFs*; regular schemes publish NAV only through AMFI.

**International / non-NSE marks** — Holdings whose ``symbol`` looks like a Yahoo
ticker (e.g. ``GC=F`` for COMEX gold) still use **yfinance** only for that narrow
case. Prefer NSE-listed gold ETFs (e.g. ``GOLDBEES``) when you want a single source.

**Orchestration** — ``refresh_all_prices`` updates the ``prices`` table and pushes
the latest close onto each market-priced ``Holding`` row.

**ICICI legacy symbols** — Broker short codes (e.g. ``APOTYR``) are mapped to NSE
bhav tickers (``APOLLOTYRE``) via :func:`canonical_nse_symbol`. If today's bhav is
missing or not yet published, we fall back to the newest ``prices`` row on or before
the target session date so holdings do not stay stuck on stale CSV marks.
"""

from __future__ import annotations

import csv
import datetime
import logging
import re
import time
from pathlib import Path
from typing import Iterable, cast

import httpx
import yfinance as yf
from nse import NSE
from sqlmodel import Session, col, func, select

from api.models import Holding, Price
from pipeline.config import REPO_ROOT
from pipeline.holding_parsers.icici_direct_equity import ICICI_SHORT_TO_NSE
from pipeline.icici_symbol_overrides import merge_with_disk
from pipeline.models import AssetClass, ValuationMethod

logger = logging.getLogger(__name__)

# AMFI moved this file behind a 302 to portal.amfiindia.com — follow redirects.
AMFI_NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

NSE_DOWNLOAD_DIR = REPO_ROOT / "data" / ".nse_cache"

# Space NSE bhav downloads slightly — the ``nse`` client also throttles (~3 rps).
_NSE_BACKFILL_SLEEP_SEC = 0.35

# Market-priced sleeves we try to mark automatically.
_MARKET_ASSET_CLASSES = frozenset(
    {
        AssetClass.EQUITY.value,
        AssetClass.MUTUAL_FUND.value,
        AssetClass.ESOP.value,
        AssetClass.GOLD.value,
        AssetClass.SOVEREIGN_GOLD_BOND.value,
    }
)

_nse_singleton: NSE | None = None


def get_nse_client() -> NSE:
    """Reuse one NSE client (cookies + throttle) per process."""
    global _nse_singleton
    NSE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if _nse_singleton is None:
        # server=False uses ``requests`` (see requirements.txt).
        _nse_singleton = NSE(str(NSE_DOWNLOAD_DIR), server=False)
    return _nse_singleton


def normalize_equity_symbol(symbol: str) -> str:
    """Canonical DB symbol: upper-case NSE ticker without ``.NS`` suffix."""
    s = symbol.strip().upper()
    for suf in (".NS", ".NSE", ".BO"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s


def canonical_nse_symbol(symbol: str) -> str:
    """NSE bhav / ``prices.symbol`` key: normalize then map legacy ICICI codes.

    Static map: ``ICICI_SHORT_TO_NSE`` in ``icici_direct_equity``; merged with
    ``data/icici_nse_symbol_overrides.json`` (optional manual overrides).
    """
    n = normalize_equity_symbol(symbol)
    merged = merge_with_disk(ICICI_SHORT_TO_NSE, "icici_short_to_nse")
    return merged.get(n, n)


def _is_international_yfinance_symbol(symbol: str) -> bool:
    """Yahoo-style tickers not on NSE bhav (e.g. ``GC=F``, ``EURUSD=X``)."""
    s = symbol.strip()
    return "=" in s


def _parse_amfi_nav_date(s: str) -> datetime.date | None:
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _amfi_line_is_category_header(line: str) -> bool:
    """AMFI section titles, e.g. ``Open Ended Schemes(Debt Scheme - ...)``."""
    s = line.strip()
    return (
        s.startswith("Open Ended Schemes")
        or s.startswith("Close Ended Schemes")
        or s.startswith("Interval Fund Schemes")
    )


def _amfi_line_is_column_header(line: str) -> bool:
    return line.strip().startswith("Scheme Code")


def _try_parse_amfi_nav_row(parts: list[str]) -> tuple[str, float, datetime.date] | None:
    """One NAV data row: scheme code, NAV, published date (supports legacy wider rows)."""
    if len(parts) < 6:
        return None
    code = parts[0].strip()
    if not code.isdigit():
        return None
    nav_s = parts[4].strip()
    d = _parse_amfi_nav_date(parts[5].strip())
    if d is None:
        d = _parse_amfi_nav_date(parts[-1].strip())
    if d is None:
        return None
    try:
        nav = float(nav_s)
    except ValueError:
        return None
    return code, nav, d


def parse_amfi_navall(
    text: str,
) -> tuple[dict[str, tuple[float, datetime.date]], dict[str, tuple[str | None, str | None]]]:
    """Parse AMFI ``NAVAll.txt``: latest NAV per scheme + (fund_category, fund_house) per code.

    Category lines set the SEBI-style bucket; the next non-empty line without ``;`` is
    usually the AMC name. Data rows carry scheme codes until the next category header.
    """
    latest: dict[str, tuple[float, datetime.date]] = {}
    meta: dict[str, tuple[str | None, str | None]] = {}
    current_category: str | None = None
    current_house: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _amfi_line_is_column_header(line):
            continue
        if _amfi_line_is_category_header(line):
            current_category = line.strip()
            current_house = None
            continue
        if ";" not in line:
            # AMC banner line, e.g. "SBI Mutual Fund"
            if len(line) >= 4:
                current_house = line.strip()
            continue

        parts = [p.strip() for p in line.split(";")]
        parsed = _try_parse_amfi_nav_row(parts)
        if parsed is None:
            continue
        code, nav, d = parsed
        prev = latest.get(code)
        if prev is None or d >= prev[1]:
            latest[code] = (nav, d)
            meta[code] = (current_category, current_house)
    return latest, meta


def parse_amfi_nav_rows(text: str) -> dict[str, tuple[float, datetime.date]]:
    """Map AMFI scheme code → (nav, as_of_date) using the latest row per code."""
    latest, _meta = parse_amfi_navall(text)
    return latest


def fetch_mf_navs(
    scheme_codes: list[str],
    as_of_date: datetime.date | None = None,
) -> list[Price]:
    """Pull NAVs for the given AMFI scheme codes from today's NAVAll file.

    If ``as_of_date`` is set, only rows whose published NAV date matches are kept
    (the public file is usually one business day — same date on every row).
    """
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        r = client.get(AMFI_NAV_ALL_URL)
        r.raise_for_status()
    by_code = parse_amfi_nav_rows(r.text)
    want = {c.strip() for c in scheme_codes if c.strip()}
    out: list[Price] = []
    for code in want:
        row = by_code.get(code)
        if not row:
            logger.warning("AMFI: no NAV row for scheme code %s", code)
            continue
        nav, nav_date = row
        if as_of_date is not None and nav_date != as_of_date:
            logger.warning(
                "AMFI: scheme %s NAV date %s != requested %s",
                code,
                nav_date,
                as_of_date,
            )
            continue
        out.append(
            Price(
                symbol=code,
                date=nav_date,
                close_price=nav,
                source="amfi",
            )
        )
    return out


def _bhav_isin_to_symbol(path: Path) -> dict[str, str]:
    """Parse UDIFF equity bhavcopy into ``{ISIN: TCKRSYMB}`` (legacy cm bhav has no ISIN)."""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return out
        hmap = {name.upper(): i for i, name in enumerate(header)}
        if "ISIN" not in hmap or "TCKRSYMB" not in hmap:
            return out
        ii, ti = hmap["ISIN"], hmap["TCKRSYMB"]
        for row in reader:
            if len(row) <= max(ii, ti):
                continue
            isin = row[ii].strip().upper()
            sym = row[ti].strip().upper()
            if isin.startswith("IN") and len(isin) == 12 and sym:
                out[isin] = sym
    return out


def _bhav_symbol_to_close(path: Path) -> dict[str, float]:
    """Parse NSE equity bhavcopy (legacy or UDIFF) into {SYMBOL: close}."""
    out: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return out
        hmap = {name.upper(): i for i, name in enumerate(header)}
        # Old cmDDMONYYYYbhav.csv
        if "SYMBOL" in hmap and "CLOSE" in hmap:
            si, ci = hmap["SYMBOL"], hmap["CLOSE"]
            for row in reader:
                if len(row) <= max(si, ci):
                    continue
                try:
                    out[row[si].strip().upper()] = float(row[ci])
                except ValueError:
                    continue
            return out
        # UDIFF BhavCopy_NSE_CM_* — TckrSymb + ClsPric (includes SGB as TckrSymb)
        if "TCKRSYMB" in hmap and "CLSPRIC" in hmap:
            si, ci = hmap["TCKRSYMB"], hmap["CLSPRIC"]
            for row in reader:
                if len(row) <= max(si, ci):
                    continue
                try:
                    out[row[si].strip().upper()] = float(row[ci])
                except ValueError:
                    continue
            return out
    logger.warning("Unrecognised bhavcopy header in %s: %s", path, header[:8])
    return out


# A real equity bhav file has thousands of symbols; empty or corrupt parses are tiny.
# We do **not** probe a specific ticker (e.g. RELIANCE) — you might not hold it.
_MIN_EQUITY_BHAV_SYMBOL_ROWS = 200


def load_nse_equity_bhav_map(trade_date: datetime.date) -> dict[str, float] | None:
    """Download and parse the full NSE equity bhavcopy for ``trade_date``.

    Returns ``None`` if the file is missing or empty. One network round-trip per call —
    used by session resolution and by :func:`fetch_equity_closes_from_nse_bhav` so we
    do not double-download during ``refresh_all_prices``.
    """
    nse = get_nse_client()
    dt = datetime.datetime.combine(trade_date, datetime.time.min)
    try:
        path = nse.equityBhavcopy(dt)
    except Exception as exc:
        logger.debug("NSE bhavcopy failed for %s: %s", trade_date, exc)
        return None
    m = _bhav_symbol_to_close(Path(path))
    return m if m else None


def _bhav_full_rows(path: Path) -> dict[str, dict[str, str]]:
    """Parse UDIFF equity bhavcopy into ``{TCKRSYMB: {COLUMN: cell}}`` (string cells)."""
    out: dict[str, dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return out
        hmap = {name.upper(): i for i, name in enumerate(header)}
        if "TCKRSYMB" not in hmap:
            return out
        ti = hmap["TCKRSYMB"]
        for row in reader:
            if len(row) <= ti:
                continue
            sym = row[ti].strip().upper()
            if not sym:
                continue
            rec: dict[str, str] = {}
            for col_name, idx in hmap.items():
                if idx < len(row):
                    rec[col_name] = row[idx]
            out[sym] = rec
    return out


def load_nse_equity_bhav_full_rows(trade_date: datetime.date) -> dict[str, dict[str, str]] | None:
    """Same bhav session file as closes; return every column per ``TCKRSYMB`` row.

    Used to cache the full official row for small-cap names and to merge bhav fields
    with Nifty index payloads for large/mid constituents.
    """
    nse = get_nse_client()
    dt = datetime.datetime.combine(trade_date, datetime.time.min)
    try:
        path = nse.equityBhavcopy(dt)
    except Exception as exc:
        logger.debug("NSE bhavcopy (full rows) failed for %s: %s", trade_date, exc)
        return None
    m = _bhav_full_rows(Path(path))
    if len(m) < _MIN_EQUITY_BHAV_SYMBOL_ROWS:
        return None
    return m


def load_nse_equity_bhav_isin_map(trade_date: datetime.date) -> dict[str, str] | None:
    """Same bhav file as :func:`load_nse_equity_bhav_map`, but ``{ISIN: NSE symbol}``.

    Returns ``None`` if the session file lacks ISIN/TCKRSYMB columns or looks incomplete.
    Used to resolve broker ISINs not present in static maps.
    """
    nse = get_nse_client()
    dt = datetime.datetime.combine(trade_date, datetime.time.min)
    try:
        path = nse.equityBhavcopy(dt)
    except Exception as exc:
        logger.debug("NSE bhavcopy (ISIN map) failed for %s: %s", trade_date, exc)
        return None
    m = _bhav_isin_to_symbol(Path(path))
    if len(m) < _MIN_EQUITY_BHAV_SYMBOL_ROWS:
        return None
    return m


def fetch_equity_closes_from_nse_bhav(
    symbols: list[str],
    trade_date: datetime.date,
) -> dict[str, float]:
    """Official NSE closing prices for one session (bhavcopy)."""
    closes = load_nse_equity_bhav_map(trade_date)
    if not closes:
        return {}
    norm = [canonical_nse_symbol(s) for s in symbols]
    return {s: closes[s] for s in norm if s in closes}


def resolve_nse_bhav_session_and_map(
    preferred: datetime.date,
    *,
    max_lookback_calendar_days: int = 10,
) -> tuple[datetime.date, dict[str, float] | None]:
    """Latest weekday on or before ``preferred`` with a usable equity bhav file.

    Returns ``(session_date, full_symbol_map)``. The map is ``None`` if no session was
    found within the lookback window — callers should treat pricing as unavailable and
    rely on ``prices``-table fallback where applicable.

    Walking back avoids using a **stale** cached ``prices`` row when today's file is
    not published yet while an earlier session's file is available.
    """
    d = preferred
    for _ in range(max_lookback_calendar_days + 1):
        if d.weekday() < 5:
            m = load_nse_equity_bhav_map(d)
            if m and len(m) >= _MIN_EQUITY_BHAV_SYMBOL_ROWS:
                return d, m
        d -= datetime.timedelta(days=1)
    return preferred, None


def resolve_nse_bhav_session_date(
    preferred: datetime.date,
    *,
    max_lookback_calendar_days: int = 10,
) -> datetime.date | None:
    """Return the session date from :func:`resolve_nse_bhav_session_and_map`, or ``None`` if no map."""
    d, m = resolve_nse_bhav_session_and_map(
        preferred, max_lookback_calendar_days=max_lookback_calendar_days
    )
    return d if m else None


def fetch_equity_prices_nse(
    symbols: list[str],
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[Price]:
    """Historical closes: one bhavcopy per calendar day (weekends skipped).

    Holidays produce no file — those days are skipped (see debug log in fetch helper).
    """
    out: list[Price] = []
    if start_date > end_date:
        return out
    norm_syms = sorted({canonical_nse_symbol(s) for s in symbols})
    d = start_date
    first = True
    while d <= end_date:
        if d.weekday() < 5:
            if not first:
                time.sleep(_NSE_BACKFILL_SLEEP_SEC)
            first = False
            nse_map = fetch_equity_closes_from_nse_bhav(norm_syms, d)
            for sym in norm_syms:
                if sym in nse_map:
                    out.append(
                        Price(
                            symbol=sym,
                            date=d,
                            close_price=nse_map[sym],
                            source="nse",
                        )
                    )
        d += datetime.timedelta(days=1)
    return out


def upsert_prices(session: Session, rows: Iterable[Price]) -> int:
    """Insert or update ``prices`` rows (unique on symbol+date). Returns count touched."""
    rows_list = list(rows)
    if not rows_list:
        return 0

    want_keys = {(p.symbol, p.date) for p in rows_list}
    want_symbols = sorted({p.symbol for p in rows_list})
    existing_rows = list(
        session.exec(
            select(Price).where(col(Price.symbol).in_(want_symbols))
        ).all()
    )
    existing_by_key = {
        (row.symbol, row.date): row
        for row in existing_rows
        if (row.symbol, row.date) in want_keys
    }

    n = 0
    for p in rows_list:
        existing = existing_by_key.get((p.symbol, p.date))
        if existing:
            existing.close_price = p.close_price
            existing.source = p.source
            session.add(existing)
        else:
            session.add(p)
        n += 1
    session.flush()
    return n


def backfill_prices(
    session: Session,
    symbol: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> dict[str, int | str]:
    """Fill gaps using NSE bhavcopy only."""
    rows = fetch_equity_prices_nse([symbol], start_date, end_date)
    if not rows:
        return {"symbol": canonical_nse_symbol(symbol), "inserted": 0, "status": "no_data"}
    inserted = upsert_prices(session, rows)
    return {"symbol": canonical_nse_symbol(symbol), "inserted": inserted, "status": "ok"}


def _select_market_priced_holdings(
    session: Session, *, user_id: str | None = None
) -> list[Holding]:
    """Active holdings we try to mark via NSE / AMFI / yfinance (same filter as refresh)."""
    q = select(Holding).where(
        Holding.is_active == True,  # noqa: E712
        Holding.valuation_method == ValuationMethod.MARKET_PRICE.value,
        col(Holding.asset_class).in_(_MARKET_ASSET_CLASSES),
    )
    if user_id:
        q = q.where(Holding.user_id == user_id)
    return list(session.exec(q).all())


def latest_bhav_target_date(as_of: datetime.date | None = None) -> datetime.date:
    """Most recent Mon–Fri on or before ``as_of`` (UTC calendar date), for NSE session alignment."""
    today = as_of if as_of is not None else datetime.datetime.now(datetime.UTC).date()
    d = today
    for _ in range(5):
        if d.weekday() < 5:
            return d
        d -= datetime.timedelta(days=1)
    return today


def nse_normalised_symbols_for_holdings(holdings: list[Holding]) -> list[str]:
    """NSE bhav symbols (normalised) implied by portfolio holdings — excludes MF codes and Yahoo intl tickers."""
    raw: list[str] = []
    for h in holdings:
        if not h.symbol:
            continue
        ac = h.asset_class
        sym = h.symbol.strip()
        if ac == AssetClass.MUTUAL_FUND.value and _is_amfi_scheme_code(sym):
            continue
        if ac == AssetClass.GOLD.value and _is_international_yfinance_symbol(sym):
            continue
        if ac in (
            AssetClass.EQUITY.value,
            AssetClass.ESOP.value,
            AssetClass.SOVEREIGN_GOLD_BOND.value,
            AssetClass.GOLD.value,
        ):
            raw.append(sym)
    return sorted({canonical_nse_symbol(s) for s in raw})


def mf_scheme_codes_for_holdings(holdings: list[Holding]) -> list[str]:
    """AMFI scheme codes (digits on ``holding.symbol``) for active MF rows — backfill NAV history."""
    raw: list[str] = []
    for h in holdings:
        if h.asset_class != AssetClass.MUTUAL_FUND.value:
            continue
        sym = (h.symbol or "").strip()
        if _is_amfi_scheme_code(sym):
            raw.append(sym)
    return sorted(dict.fromkeys(raw))


def market_priced_holdings(session: Session, *, user_id: str | None = None) -> list[Holding]:
    """Same filter as :func:`refresh_all_prices` (NSE + MF + intl gold tickers)."""
    return _select_market_priced_holdings(session, user_id=user_id)


def calendar_start_for_forced_nse_depth(
    latest_session: datetime.date,
    *,
    depth_calendar_days: int,
    weekend_holiday_buffer_days: int = 14,
) -> datetime.date:
    """Calendar start date when forcing ~``depth_calendar_days`` of NSE bhav history.

    Weekends and exchange holidays have no bhav file; the buffer pulls extra calendar
    days so the walk from ``start`` → ``latest_session`` still covers about a year of
    trading sessions.  Used by the one-shot backfill script, not by startup sync.
    """
    return latest_session - datetime.timedelta(
        days=depth_calendar_days + weekend_holiday_buffer_days
    )


def has_market_priced_holdings(session: Session, *, user_id: str | None = None) -> bool:
    """True if any row would be picked up by :func:`refresh_all_prices`."""
    q = select(Holding.id).where(
        Holding.is_active == True,  # noqa: E712
        Holding.valuation_method == ValuationMethod.MARKET_PRICE.value,
        col(Holding.asset_class).in_(_MARKET_ASSET_CLASSES),
    )
    if user_id:
        q = q.where(Holding.user_id == user_id)
    return session.exec(q.limit(1)).first() is not None


def backfill_nse_portfolio_gaps(
    session: Session,
    *,
    user_id: str | None = None,
    max_calendar_lookback_if_empty: int = 120,
) -> dict[str, object]:
    """Insert missing NSE ``prices`` rows when any portfolio symbol is behind the latest weekday session.

    Mutual funds and international Yahoo symbols are **not** backfilled here (use
    ``scripts/backfill_price_history.py`` for MF history; yfinance is refresh-only). After this, call
    :func:`refresh_all_prices` to update MF / intl and push marks onto ``Holding`` rows.

    ``max_calendar_lookback_if_empty`` caps how far we walk back when a symbol has **no** ``prices``
    rows yet (avoids a 30-minute first boot when the DB is empty).
    """
    holdings = _select_market_priced_holdings(session, user_id=user_id)
    symbols = nse_normalised_symbols_for_holdings(holdings)
    if not symbols:
        return {"symbols": [], "target": None, "details": []}

    target = latest_bhav_target_date()
    today = datetime.datetime.now(datetime.UTC).date()
    details: list[dict[str, int | str]] = []

    for sym in symbols:
        # ``MAX(date)`` is NULL when there are no rows; stubs often type ``.one()`` as non-optional.
        last_d = cast(
            datetime.date | None,
            session.exec(
                select(func.max(Price.date)).where(Price.symbol == sym)
            ).one(),
        )
        if last_d is not None and last_d >= target:
            continue

        if last_d is None:
            start = target - datetime.timedelta(days=max_calendar_lookback_if_empty)
        else:
            # Only reached when ``last_d < target`` (otherwise we ``continue`` above).
            start = last_d + datetime.timedelta(days=1)

        res = backfill_prices(session, sym, start, target)
        details.append(res)

    return {
        "symbols": symbols,
        "target": target.isoformat(),
        "as_of_calendar": today.isoformat(),
        "details": details,
    }


def run_startup_price_sync(session: Session) -> dict[str, object]:
    """Phase A.4.2 — after ``init_db()``: backfill stale NSE history, then one full refresh.

    Safe when there are no market-priced holdings (no-op). Caller should ``commit()`` the session.
    """
    if not has_market_priced_holdings(session):
        logger.info(
            "No holdings need live prices yet — skipped startup price refresh."
        )
        return {"skipped": True, "reason": "no_market_holdings"}

    bf = backfill_nse_portfolio_gaps(session)
    refreshed = refresh_all_prices(session)
    details_raw = bf.get("details", [])
    n_detail_rows = len(details_raw) if isinstance(details_raw, list) else 0
    _as_of = refreshed.get("as_of")
    logger.info(
        "Prices refreshed for your holdings%s.",
        f" (as of {_as_of})" if _as_of else "",
    )
    logger.debug(
        "Startup price detail — backfill_detail_rows=%d refresh=%s",
        n_detail_rows,
        refreshed,
    )
    return {"backfill": bf, "refresh": refreshed}


def _is_amfi_scheme_code(symbol: str | None) -> bool:
    if not symbol:
        return False
    return bool(re.fullmatch(r"\d{4,7}", symbol.strip()))


def _latest_close_on_or_before(
    session: Session, symbol: str, as_of: datetime.date
) -> tuple[float, datetime.date] | None:
    """Best prior close in ``prices`` for NSE-style ``symbol`` (already canonical)."""
    # Select the full row so mypy/SQLAlchemy agree on ``select()`` overloads (scalar
    # columns are typed as Python float/date on the model, which confuses select()).
    q = (
        select(Price)
        .where(Price.symbol == symbol, Price.date <= as_of)
        .order_by(col(Price.date).desc())
        .limit(1)
    )
    row = session.exec(q).first()
    if row is None:
        return None
    return (float(row.close_price), row.date)


def refresh_all_prices(session: Session, *, user_id: str | None = None) -> dict[str, object]:
    """Refresh last close for every active market-priced holding.

    * **NSE bhavcopy** — equities, ESOP, SGB, and Indian gold **ETF** tickers on NSE.
    * **AMFI** — open-ended mutual funds (numeric scheme code on ``holding.symbol``).
    * **yfinance** — only when ``symbol`` contains ``=`` (e.g. ``GC=F``).

    Caller should ``session.commit()`` when embedding in a request transaction.
    """
    holdings = _select_market_priced_holdings(session, user_id=user_id)

    preferred = latest_bhav_target_date()
    session_d, full_bhav_map = resolve_nse_bhav_session_and_map(preferred)
    if full_bhav_map and session_d < preferred:
        logger.debug(
            "NSE bhav session — using %s (preferred %s not published yet)",
            session_d,
            preferred,
        )
    d = session_d

    nse_symbols: list[str] = []
    mf_codes: list[str] = []
    intl_gold: list[tuple[int, str]] = []

    for h in holdings:
        if not h.symbol:
            continue
        ac = h.asset_class
        sym = h.symbol.strip()

        if ac == AssetClass.MUTUAL_FUND.value and _is_amfi_scheme_code(sym):
            mf_codes.append(sym)
            continue

        if ac == AssetClass.GOLD.value and _is_international_yfinance_symbol(sym):
            if h.id is not None:
                intl_gold.append((h.id, sym))
            continue

        if ac in (
            AssetClass.EQUITY.value,
            AssetClass.ESOP.value,
            AssetClass.SOVEREIGN_GOLD_BOND.value,
            AssetClass.GOLD.value,
        ):
            nse_symbols.append(sym)

    nse_symbols = list({canonical_nse_symbol(s) for s in nse_symbols})
    mf_codes = list(dict.fromkeys(mf_codes))

    price_rows: list[Price] = []
    nse_map: dict[str, float] = {}
    if nse_symbols:
        if full_bhav_map:
            nse_map = {s: full_bhav_map[s] for s in nse_symbols if s in full_bhav_map}
        else:
            nse_map = {}

    for sym in nse_symbols:
        close = nse_map.get(sym)
        row_date = d
        source = "nse"
        if close is None:
            fb = _latest_close_on_or_before(session, sym, d)
            if fb is not None:
                close, row_date = fb
                source = "nse_cached"
                logger.debug(
                    "Using last saved price for %s — that day's exchange list had no row "
                    "(close %.4f from %s)",
                    sym,
                    close,
                    row_date,
                )
            else:
                logger.warning(
                    "Couldn't refresh the live price for %s (%s) — your holding stays "
                    "at its last value until we get data.",
                    sym,
                    d.strftime("%d %b %Y"),
                )
                logger.debug(
                    "Price gap — symbol=%s session_date=%s (no daily price on file yet)",
                    sym,
                    d.isoformat(),
                )
                continue
        price_rows.append(
            Price(symbol=sym, date=row_date, close_price=close, source=source)
        )

    for code in mf_codes:
        price_rows.extend(fetch_mf_navs([code], as_of_date=None))

    for _hid, gt in intl_gold:
        try:
            hist = yf.Ticker(gt).history(period="5d", auto_adjust=False)
        except Exception as exc:
            logger.warning("yfinance failed for international symbol %s: %s", gt, exc)
            continue
        if hist is None or hist.empty:
            logger.warning("yfinance: no rows for international symbol %s", gt)
            continue
        last = hist.iloc[-1]
        close = float(last["Close"])
        idx = hist.index[-1]
        d_row = idx.date() if hasattr(idx, "date") else idx
        if isinstance(d_row, datetime.datetime):
            d_row = d_row.date()
        price_rows.append(Price(symbol=gt, date=d_row, close_price=close, source="yfinance"))

    upserted = upsert_prices(session, price_rows)

    close_by_symbol: dict[str, tuple[float, datetime.date, str]] = {}
    for p in price_rows:
        close_by_symbol[p.symbol] = (p.close_price, p.date, p.source)

    holdings_updated = 0
    for h in holdings:
        if not h.symbol:
            continue
        key = h.symbol.strip()
        if h.asset_class == AssetClass.MUTUAL_FUND.value and _is_amfi_scheme_code(key):
            lookup = key
        elif h.asset_class == AssetClass.GOLD.value and _is_international_yfinance_symbol(key):
            lookup = key
        else:
            lookup = canonical_nse_symbol(h.symbol)
        tup = close_by_symbol.get(lookup)
        if not tup:
            continue
        close, row_date, _src = tup
        h.current_price_per_unit = close
        if h.quantity is not None:
            h.current_value = float(h.quantity) * close
        h.last_valued_date = row_date
        session.add(h)
        holdings_updated += 1

    session.flush()
    return {
        "as_of": d.isoformat(),
        "price_rows_upserted": upserted,
        "holdings_updated": holdings_updated,
        "nse_symbols": nse_symbols,
        "mf_codes": mf_codes,
        "international_yfinance_symbols": [s for _, s in intl_gold],
    }
