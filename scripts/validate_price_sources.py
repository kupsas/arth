#!/usr/bin/env python3
"""
Phase A.1.2a — NSE bhavcopy vs yfinance close comparison.

Run from repo root::

    python3 scripts/validate_price_sources.py
    python3 scripts/validate_price_sources.py --only-investigate

**Why can Yahoo and NSE disagree?**  NSE bhavcopy is the exchange's **raw** close
for that session. Yahoo often **back-adjusts** older bars after splits/bonus issues
so past prices line up with today's share count — so ``Close`` with
``auto_adjust=False`` can still disagree with bhav for affected names (you may see
ratios like 0.5× or 0.67× vs NSE).  Always validate tickers you care about.

This downloads official NSE **equity bhavcopy** files for a handful of historical
dates, parses the closing price, and compares to **yfinance** (``TICKER.NS``) on the
same calendar day.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from collections import defaultdict
from pathlib import Path

# Repo root on sys.path so `import api` works when run as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yfinance as yf  # noqa: E402

from api.services.price_feed import (  # noqa: E402
    fetch_equity_closes_from_nse_bhav,
    normalize_equity_symbol,
)


def to_yfinance_ticker(symbol: str) -> str:
    """Diagnostic only — Yahoo ``TICKER.NS`` (not used by production ``price_feed``)."""
    return f"{normalize_equity_symbol(symbol)}.NS"

# Small set that matched well in prior runs (sanity check).
BASELINE_SYMBOLS = ["TCS", "INFY", "SBIN"]

# Ten *other* liquid NSE names — excludes HDFCBANK / RELIANCE / baseline trio.
# Mix: banks, FMCG, auto, paint, telecom, IT, industrials.
INVESTIGATION_SYMBOLS = [
    "ITC",
    "ICICIBANK",
    "KOTAKBANK",
    "BHARTIARTL",
    "LT",
    "HINDUNILVR",
    "WIPRO",
    "MARUTI",
    "ASIANPAINT",
    "TITAN",
]

# Mix of recent UDIFF bhav and older cm* format (pre–Jul 2024).
SAMPLE_DATES = [
    datetime.date(2025, 6, 2),
    datetime.date(2025, 3, 10),
    datetime.date(2024, 11, 5),
    datetime.date(2023, 1, 2),
    datetime.date(2022, 8, 12),
]

OK_THRESHOLD_PCT = 0.25


def yfinance_close_on(symbol: str, d: datetime.date) -> float | None:
    """Single-session close from Yahoo (unadjusted Close column)."""
    t = yf.Ticker(to_yfinance_ticker(symbol))
    end = d + datetime.timedelta(days=1)
    hist = t.history(start=d, end=end, auto_adjust=False)
    if hist is None or hist.empty:
        return None
    try:
        return float(hist.iloc[-1]["Close"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def run_batch(
    title: str,
    symbols: list[str],
    dates: list[datetime.date],
) -> dict[str, dict[str, float | int | None]]:
    """Print per-date lines; return per-symbol stats (max_diff_pct, ok_count, ratio_yf_over_nse)."""
    print(f"{title}\n")
    # symbol -> list of (nse, yf) pairs where both present
    pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for d in dates:
        print(f"=== {d.isoformat()} (weekday {d.weekday()}) ===")
        nse_map = fetch_equity_closes_from_nse_bhav(symbols, d)
        for sym in symbols:
            key = normalize_equity_symbol(sym)
            nse_c = nse_map.get(key)
            yf_c = yfinance_close_on(sym, d)
            if nse_c is None and yf_c is None:
                print(f"  {sym}: no data from either source (holiday?)")
                continue
            if nse_c is None:
                print(f"  {sym}: NSE missing, yfinance={yf_c:.4f}")
                continue
            if yf_c is None:
                print(f"  {sym}: yfinance missing, NSE={nse_c:.4f}")
                continue
            diff_pct = abs(nse_c - yf_c) / nse_c * 100.0 if nse_c else 0.0
            flag = "OK" if diff_pct < OK_THRESHOLD_PCT else "CHECK"
            ratio = yf_c / nse_c if nse_c else None
            print(
                f"  {sym}: NSE={nse_c:.4f}  YF={yf_c:.4f}  "
                f"diff={diff_pct:.3f}%  YF/NSE={ratio:.6f}  [{flag}]"
            )
            pairs[sym].append((nse_c, yf_c))
        print()

    stats: dict[str, dict[str, float | int | None]] = {}
    for sym in symbols:
        row = pairs.get(sym, [])
        if not row:
            stats[sym] = {"max_diff_pct": None, "ok_count": 0, "check_count": 0, "median_ratio": None}
            continue
        max_diff = 0.0
        ok_c = chk_c = 0
        ratios: list[float] = []
        for nse_c, yf_c in row:
            d_pct = abs(nse_c - yf_c) / nse_c * 100.0 if nse_c else 0.0
            max_diff = max(max_diff, d_pct)
            if d_pct < OK_THRESHOLD_PCT:
                ok_c += 1
            else:
                chk_c += 1
            if nse_c:
                ratios.append(yf_c / nse_c)
        ratios.sort()
        mid = ratios[len(ratios) // 2]
        stats[sym] = {
            "max_diff_pct": max_diff,
            "ok_count": ok_c,
            "check_count": chk_c,
            "median_ratio": mid,
        }
    return stats


def print_summary(stats: dict[str, dict[str, float | int | None]], title: str) -> None:
    print(f"--- {title} (threshold OK = diff < {OK_THRESHOLD_PCT}%) ---")
    print(f"{'Symbol':<12} {'OK':>4} {'CHK':>4} {'max_diff%':>10} {'median YF/NSE':>14}")
    for sym in sorted(stats.keys()):
        s = stats[sym]
        md = s["max_diff_pct"]
        md_s = f"{md:.3f}" if isinstance(md, float) else "n/a"
        mr = s["median_ratio"]
        mr_s = f"{mr:.6f}" if isinstance(mr, float) else "n/a"
        print(
            f"{sym:<12} {s['ok_count']!s:>4} {s['check_count']!s:>4} "
            f"{md_s:>10} {mr_s:>14}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare NSE bhav vs yfinance closes.")
    parser.add_argument(
        "--only-investigate",
        action="store_true",
        help="Skip baseline trio; run only the 10-ticker investigation set.",
    )
    args = parser.parse_args()

    print("NSE bhavcopy vs yfinance (Close, auto_adjust=False)\n")

    if not args.only_investigate:
        baseline_stats = run_batch("## Baseline (known-good sample)", BASELINE_SYMBOLS, SAMPLE_DATES)
        print_summary(baseline_stats, "Baseline summary")

    inv_stats = run_batch("## Investigation (10 other tickers)", INVESTIGATION_SYMBOLS, SAMPLE_DATES)
    print_summary(inv_stats, "Investigation summary")


if __name__ == "__main__":
    main()
