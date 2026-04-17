#!/usr/bin/env python3
"""
Check whether NSE ``lookup()`` can recover the same equity ticker you already store
for **ICICI Direct** (and similar) holdings.

**Why this exists:** Demat exports and broker PDFs often carry **company names** or odd
labels. If ``lookup(company_name)`` returns your canonical NSE symbol in the top hits,
you can resolve ambiguous strings without an LLM — validate with
:func:`api.services.nse_ticker_resolve.ticker_valid_for_nse_equity` before trusting.

**What it does (read-only):**
1. Load active market-priced equity / ESOP / gold-ETF / SGB rows for a chosen
   ``account_platform`` (default: ``ICICI Direct``).
2. For each row, call ``NSE.lookup()`` with:
   - **name_query** — first line of ``holding.name`` (what ICICI-style imports usually carry).
   - **symbol_query** — the stored ``holding.symbol`` after :func:`canonical_nse_symbol`
     (sanity check: lookup should find itself for real NSE tickers).
3. Print whether your DB symbol appears among the first *N* equity hits from each query.

**Run from repo root** (uses ``arth.db`` via ``api.database`` — same as other scripts)::

    python3 scripts/validate_icici_nse_lookup.py
    python3 scripts/validate_icici_nse_lookup.py --user-id sashank
    python3 scripts/validate_icici_nse_lookup.py --platform \"ICICI Direct\" --top 8

**Requirements:** Network access to NSE; first run may download cookies/bhav into
``data/.nse_cache`` like the rest of the app.

Does **not** modify the database.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlmodel import Session, col, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import Holding  # noqa: E402
from api.services.price_feed import canonical_nse_symbol, get_nse_client  # noqa: E402
from pipeline.models import AssetClass, ValuationMethod  # noqa: E402

_LOOKUP_THROTTLE_SEC = 0.36


def _first_line(text: str) -> str:
    return (text or "").strip().split("\n")[0].strip()


def _equity_symbols_from_lookup(data: dict, *, limit: int) -> list[str]:
    """Ordered NSE equity tickers from a ``lookup()`` payload (best-effort)."""
    out: list[str] = []
    for row in data.get("symbols") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("result_sub_type") or "").lower() != "equity":
            continue
        sym = (row.get("symbol") or "").strip().upper()
        if sym:
            out.append(sym)
        if len(out) >= limit:
            break
    return out


def _rank_match(hits: list[str], want: str) -> int | None:
    """1-based index if ``want`` appears in ``hits``, else ``None``."""
    for i, h in enumerate(hits):
        if h == want:
            return i + 1
    return None


def _run_lookup(nse, query: str) -> list[str]:
    if not query:
        return []
    try:
        data = nse.lookup(query)
    except Exception as exc:
        print(f"  [lookup error] query={query!r} err={exc}")
        return []
    if not isinstance(data, dict):
        return []
    return _equity_symbols_from_lookup(data, limit=50)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user-id", default=None, help="Optional filter on Holding.user_id")
    p.add_argument(
        "--platform",
        default="ICICI Direct",
        help='account_platform filter (default: "ICICI Direct")',
    )
    p.add_argument(
        "--top",
        type=int,
        default=10,
        help="Max equity hits to consider per lookup call (default: 10)",
    )
    p.add_argument(
        "--include-inactive",
        action="store_true",
        help="Also scan holdings with is_active=False",
    )
    args = p.parse_args(argv)

    init_db()
    engine = get_engine()
    plat = (args.platform or "").strip()
    if not plat:
        print("ERROR: --platform must be non-empty", file=sys.stderr)
        return 2
    uid = (args.user_id or "").strip()

    asset_classes = (
        AssetClass.EQUITY.value,
        AssetClass.ESOP.value,
        AssetClass.GOLD.value,
        AssetClass.SOVEREIGN_GOLD_BOND.value,
    )

    with Session(engine) as session:
        q = select(Holding).where(
            Holding.account_platform == plat,
            Holding.valuation_method == ValuationMethod.MARKET_PRICE.value,
            col(Holding.asset_class).in_(asset_classes),
            col(Holding.symbol).is_not(None),
        )
        if not args.include_inactive:
            q = q.where(Holding.is_active == True)  # noqa: E712
        if uid:
            q = q.where(Holding.user_id == uid)
        q = q.order_by(col(Holding.user_id), col(Holding.id))
        rows = list(session.exec(q).all())

    if not rows:
        print(f"No holdings found for platform={plat!r} (try --include-inactive or another --platform).")
        return 0

    nse = get_nse_client()
    top = max(1, min(int(args.top), 50))

    print(
        f"rows={len(rows)} platform={plat!r} top={top} user_id={uid or '*'}\n"
        "Columns: id | user | db_symbol | name_hit | sym_hit | name (truncated)\n"
        "  name_hit = rank of db_symbol in lookup(first_line(name));  ok = in top-N;  . = missing\n"
        "  sym_hit  = rank in lookup(db_symbol); expect ok for valid NSE tickers\n"
    )

    mismatches = 0
    for h in rows:
        db_sym = canonical_nse_symbol((h.symbol or "").strip())
        if not db_sym:
            continue
        name_q = _first_line(h.name)
        # Name-only query: trim very long strings (NSE search may behave oddly)
        if len(name_q) > 120:
            name_q = name_q[:120].rstrip()

        time.sleep(_LOOKUP_THROTTLE_SEC)
        name_hits = _run_lookup(nse, name_q)[:top]
        time.sleep(_LOOKUP_THROTTLE_SEC)
        sym_hits = _run_lookup(nse, db_sym)[:top]

        nr = _rank_match(name_hits, db_sym)
        sr = _rank_match(sym_hits, db_sym)
        name_ok = nr is not None
        sym_ok = sr is not None

        if not name_ok:
            mismatches += 1

        nh = ",".join(name_hits[:5]) if name_hits else "—"
        sh = ",".join(sym_hits[:5]) if sym_hits else "—"
        n_disp = f"{nr}" if nr is not None else "."
        s_disp = f"{sr}" if sr is not None else "."

        nm_short = (h.name or "")[:72] + ("…" if len(h.name or "") > 72 else "")
        print(
            f"{h.id}\t{h.user_id}\t{db_sym}\t{n_disp}\t{s_disp}\t{nm_short}\n"
            f"    lookup(name)[:5]={nh}\n"
            f"    lookup(sym)[:5]={sh}\n"
        )

    print(f"done. rows_scanned={len(rows)} name_lookup_misses_in_top{top}={mismatches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
