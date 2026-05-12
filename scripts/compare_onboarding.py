#!/usr/bin/env python3
"""
Compare a **reference** SQLite database (typically a frozen ``arth_main.db`` or
``arth_reference.db``) against a **candidate** produced by onboarding (e.g.
``arth_onboarding.db``).

Two alignment modes are reported:

1. **content_hash** — canonical dedupe key (strict; email vs statement often
   produces different hashes for the same money movement).

2. **Fuzzy match** — one-to-one pairing on ``(account_id, direction, amount)``
   with **date within ±N calendar days** (default N=1, same spirit as bank
   reconciliation in ``db_writer``). Uses maximum bipartite matching inside
   each bucket so duplicate same-day / same-amount rows get distinct pairs when
   possible.

**Typical usage** (from repo root)::

    # After copying a baseline:  cp data/arth_main.db data/arth_reference.db
    python3 scripts/compare_onboarding.py \\
        --reference data/arth_reference.db \\
        --candidate data/arth_onboarding.db

Defaults point at ``data/arth_main.db`` vs ``data/arth_onboarding.db`` so a quick
check works once those files exist.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# Repo root — keep paths stable no matter where you invoke the script from.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open read-only SQLite so we never mutate either database by accident."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _normalize_counterparty(value: str | None) -> str | None:
    """Lowercase + trim + collapse whitespace — same spirit as API fuzzy matching."""
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


def _norm_optional_str(value: str | None) -> str | None:
    """Treat NULL and empty string as “no value” so comparisons are stable."""
    if value is None:
        return None
    s = value.strip()
    return s if s else None


def _bucket_classification_source(src: str | None) -> str:
    """Coarse buckets for the summary line (rules / LLM / user / unclassified / other)."""
    if src is None or not str(src).strip():
        return "unclassified"
    u = str(src).strip().upper()
    if u.startswith("RULES"):
        return "rules"
    if u.startswith("LLM"):
        return "llm"
    if u.startswith("USER"):
        return "user"
    return "other"


def _txn_date_value(row: sqlite3.Row) -> dt.date:
    """Parse ``txn_date`` from SQLite (DATE as str or rarely datetime)."""
    v = row["txn_date"]
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        return dt.date.fromisoformat(v[:10])
    raise TypeError(f"Unexpected txn_date type: {type(v)!r}")


def _amount_key(amount: Any, decimals: int) -> float:
    """Round amount so tiny float noise does not split buckets."""
    return round(float(amount or 0.0), decimals)


def _fuzzy_bucket_key(row: sqlite3.Row, amount_decimals: int) -> tuple[str, str, float]:
    """Group rows for fuzzy pairing: same account, direction, and rounded amount."""
    direction = _norm_optional_str(row["direction"]) or ""
    return (str(row["account_id"]), direction, _amount_key(row["amount"], amount_decimals))


def _load_txn_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Load all ``transactions`` rows (one row per ``content_hash`` in a healthy DB)."""
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT content_hash, account_id, txn_date, direction, amount,
               txn_type, counterparty, counterparty_category, spend_category,
               classification_source, source_type
        FROM transactions
        """
    )
    return list(cur.fetchall())


def _account_rollups(rows: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    """Per ``account_id``: transaction count and min/max ``txn_date`` (ISO strings)."""
    by_acct: dict[str, dict[str, Any]] = {}
    for r in rows:
        aid = r["account_id"]
        if aid not in by_acct:
            by_acct[aid] = {"count": 0, "min_date": r["txn_date"], "max_date": r["txn_date"]}
        slot = by_acct[aid]
        slot["count"] += 1
        d = r["txn_date"]
        if d and slot["min_date"] and d < slot["min_date"]:
            slot["min_date"] = d
        if d and slot["max_date"] and d > slot["max_date"]:
            slot["max_date"] = d
    return by_acct


def _source_distribution(rows: list[sqlite3.Row]) -> Counter[str]:
    return Counter(_bucket_classification_source(r["classification_source"]) for r in rows)


def _format_pct(num: float, den: float) -> str:
    if den <= 0:
        return "n/a"
    return f"{100.0 * num / den:.1f}%"


def _format_dist(counter: Counter[str], total: int) -> str:
    if total <= 0:
        return "(no transactions)"
    parts = []
    order = ("rules", "llm", "user", "unclassified", "other")
    for key in order:
        n = counter.get(key, 0)
        if n:
            parts.append(f"{key.capitalize()} {_format_pct(n, total)}")
    # Any unexpected keys
    for k, n in sorted(counter.items()):
        if k not in order and n:
            parts.append(f"{k} {_format_pct(n, total)}")
    return " | ".join(parts) if parts else "(empty)"


def _compare_overlap_rows(ref_row: sqlite3.Row, cand_row: sqlite3.Row) -> dict[str, bool]:
    """Field-by-field agreement for one aligned pair (reference vs candidate)."""
    return {
        "txn_type": _norm_optional_str(ref_row["txn_type"]) == _norm_optional_str(cand_row["txn_type"]),
        "counterparty": _normalize_counterparty(ref_row["counterparty"])
        == _normalize_counterparty(cand_row["counterparty"]),
        "counterparty_category": _norm_optional_str(ref_row["counterparty_category"])
        == _norm_optional_str(cand_row["counterparty_category"]),
        "spend_category": _norm_optional_str(ref_row["spend_category"])
        == _norm_optional_str(cand_row["spend_category"]),
    }


def _aggregate_pair_stats(pairs: list[tuple[sqlite3.Row, sqlite3.Row]]) -> dict[str, Any]:
    """Counts for classification agreement over a list of (ref, cand) row pairs."""
    n = len(pairs)
    if n == 0:
        return {
            "n": 0,
            "txn_type": 0,
            "counterparty": 0,
            "counterparty_category": 0,
            "spend_category": 0,
            "all_four": 0,
        }
    m_tt = m_cp = m_cc = m_sc = all_four = 0
    for rr, cr in pairs:
        cmp_row = _compare_overlap_rows(rr, cr)
        if cmp_row["txn_type"]:
            m_tt += 1
        if cmp_row["counterparty"]:
            m_cp += 1
        if cmp_row["counterparty_category"]:
            m_cc += 1
        if cmp_row["spend_category"]:
            m_sc += 1
        if all(cmp_row.values()):
            all_four += 1
    return {
        "n": n,
        "txn_type": m_tt,
        "counterparty": m_cp,
        "counterparty_category": m_cc,
        "spend_category": m_sc,
        "all_four": all_four,
    }


def _breakdown_by_source_type(rows: list[sqlite3.Row], hashes: set[str]) -> Counter[str]:
    c: Counter[str] = Counter()
    for r in rows:
        if r["content_hash"] in hashes:
            st = r["source_type"] or "(null)"
            c[st] += 1
    return c


def _breakdown_indices_by_source_type(rows: list[sqlite3.Row], indices: set[int]) -> Counter[str]:
    """Like ``_breakdown_by_source_type`` but keyed by row index in ``rows``."""
    c: Counter[str] = Counter()
    for i in indices:
        st = rows[i]["source_type"] or "(null)"
        c[st] += 1
    return c


def _max_bipartite_match_pairs(
    ref_indices: list[int],
    cand_indices: list[int],
    ref_rows: list[sqlite3.Row],
    cand_rows: list[sqlite3.Row],
    date_slop_days: int,
) -> list[tuple[int, int]]:
    """Maximum cardinality matching: left = ref slot, right = cand slot; edge if dates within slop.

    Returns list of (global_ref_index, global_cand_index).
    """
    nr, nc = len(ref_indices), len(cand_indices)
    if nr == 0 or nc == 0:
        return []

    # Local indices 0..nr-1, 0..nc-1 — adjacency from each ref local to cand locals within date slop.
    adj: list[list[int]] = [[] for _ in range(nr)]
    for li in range(nr):
        dr = _txn_date_value(ref_rows[ref_indices[li]])
        for lj in range(nc):
            dc = _txn_date_value(cand_rows[cand_indices[lj]])
            if abs((dr - dc).days) <= date_slop_days:
                adj[li].append(lj)

    # Kuhn's algorithm: match_r[u] = local left index matched to right u (-1 if free).
    match_r: list[int] = [-1] * nc

    def dfs(v: int, seen: list[bool]) -> bool:
        for u in adj[v]:
            if seen[u]:
                continue
            seen[u] = True
            if match_r[u] == -1 or dfs(match_r[u], seen):
                match_r[u] = v
                return True
        return False

    for v in range(nr):
        seen = [False] * nc
        dfs(v, seen)

    out: list[tuple[int, int]] = []
    for u in range(nc):
        v = match_r[u]
        if v != -1:
            out.append((ref_indices[v], cand_indices[u]))
    return out


def _fuzzy_global_matching(
    ref_rows: list[sqlite3.Row],
    cand_rows: list[sqlite3.Row],
    *,
    date_slop_days: int,
    amount_decimals: int,
) -> list[tuple[int, int]]:
    """Pair ref rows to cand rows using per-bucket bipartite matching."""
    # ref index -> list in bucket key
    ref_by_bucket: dict[tuple[str, str, float], list[int]] = defaultdict(list)
    for i, r in enumerate(ref_rows):
        ref_by_bucket[_fuzzy_bucket_key(r, amount_decimals)].append(i)

    cand_by_bucket: dict[tuple[str, str, float], list[int]] = defaultdict(list)
    for j, c in enumerate(cand_rows):
        cand_by_bucket[_fuzzy_bucket_key(c, amount_decimals)].append(j)

    pairs: list[tuple[int, int]] = []
    all_keys = set(ref_by_bucket.keys()) | set(cand_by_bucket.keys())
    for key in all_keys:
        ris = sorted(ref_by_bucket.get(key, []))
        cjs = sorted(cand_by_bucket.get(key, []))
        pairs.extend(_max_bipartite_match_pairs(ris, cjs, ref_rows, cand_rows, date_slop_days))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        type=Path,
        default=REPO_ROOT / "data" / "arth_main.db",
        help="Reference / gold SQLite file (default: data/arth_main.db).",
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        default=REPO_ROOT / "data" / "arth_onboarding.db",
        help="Candidate DB from onboarding (default: data/arth_onboarding.db).",
    )
    parser.add_argument(
        "--date-slop-days",
        type=int,
        default=1,
        help="Max |ref_date - cand_date| in days for a fuzzy pair (default: 1).",
    )
    parser.add_argument(
        "--amount-decimals",
        type=int,
        default=2,
        help="Round amounts to this many decimals for fuzzy bucketing (default: 2).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-account counts and date ranges for both databases.",
    )
    args = parser.parse_args()

    ref_path = args.reference.expanduser().resolve()
    cand_path = args.candidate.expanduser().resolve()

    if not ref_path.is_file():
        print(f"ERROR: reference DB not found: {ref_path}", file=sys.stderr)
        return 1
    if not cand_path.is_file():
        print(f"ERROR: candidate DB not found: {cand_path}", file=sys.stderr)
        return 1

    ref_conn = _connect(ref_path)
    cand_conn = _connect(cand_path)
    try:
        ref_list = _load_txn_rows(ref_conn)
        cand_list = _load_txn_rows(cand_conn)
    except sqlite3.Error as exc:
        print(f"ERROR reading transactions table: {exc}", file=sys.stderr)
        return 1
    finally:
        ref_conn.close()
        cand_conn.close()

    # ------------------------------------------------------------------ hash mode
    ref_by_hash = {r["content_hash"]: r for r in ref_list}
    cand_by_hash = {r["content_hash"]: r for r in cand_list}
    ref_hashes = set(ref_by_hash.keys())
    cand_hashes = set(cand_by_hash.keys())
    overlap = ref_hashes & cand_hashes

    missing = ref_hashes - cand_hashes
    new_only = cand_hashes - ref_hashes

    n_ref = len(ref_hashes)
    n_cand = len(cand_hashes)
    n_overlap = len(overlap)
    coverage_pct = 100.0 * n_overlap / n_ref if n_ref else 0.0

    hash_pairs = [(ref_by_hash[h], cand_by_hash[h]) for h in overlap]
    stats = _aggregate_pair_stats(hash_pairs)
    n_stats = stats["n"]
    class_match_pct = 100.0 * stats["all_four"] / n_stats if n_stats else 0.0

    ref_src = _source_distribution(ref_list)
    cand_src = _source_distribution(cand_list)

    print("=== Onboarding Quality Report ===")
    print(f"Reference:  {ref_path}")
    print(f"Candidate: {cand_path}")
    print()
    print("--- Transaction coverage (content_hash) ---")
    print(f"Reference transactions:   {n_ref:,}")
    print(f"Candidate transactions: {n_cand:,}")
    print(f"Transaction coverage:   {coverage_pct:.1f}% ({n_overlap:,} / {n_ref:,})")
    print(f"Missing from candidate: {len(missing):,} transaction(s)")
    print(f"New from candidate:     {len(new_only):,} transaction(s)")
    if missing:
        mb = _breakdown_by_source_type(ref_list, missing)
        print(f"  Missing breakdown (ref source_type): {dict(mb)}")
    if new_only:
        nb = _breakdown_by_source_type(cand_list, new_only)
        print(f"  New-only breakdown (cand source_type): {dict(nb)}")

    if args.verbose:
        print()
        print("--- Per-account (reference) ---")
        for acct, info in sorted(_account_rollups(ref_list).items()):
            print(
                f"  {acct}: count={info['count']:,} "
                f"dates {info['min_date']} .. {info['max_date']}"
            )
        print("--- Per-account (candidate) ---")
        for acct, info in sorted(_account_rollups(cand_list).items()):
            print(
                f"  {acct}: count={info['count']:,} "
                f"dates {info['min_date']} .. {info['max_date']}"
            )

    print()
    print("--- Classification agreement (overlapping content_hash) ---")
    if n_stats:
        print(f"Overlapping rows: {n_stats:,}")
        print(f"txn_type match:              {_format_pct(stats['txn_type'], n_stats)} ({stats['txn_type']:,} / {n_stats:,})")
        print(f"counterparty match (fuzzy): {_format_pct(stats['counterparty'], n_stats)} ({stats['counterparty']:,} / {n_stats:,})")
        print(
            f"counterparty_category match: {_format_pct(stats['counterparty_category'], n_stats)} "
            f"({stats['counterparty_category']:,} / {n_stats:,})"
        )
        print(f"spend_category match:        {_format_pct(stats['spend_category'], n_stats)} ({stats['spend_category']:,} / {n_stats:,})")
        print(f"All four (normalized) match: {class_match_pct:.1f}% ({stats['all_four']:,} / {n_stats:,})")
    else:
        print("(no overlapping hashes — cannot compare classification)")

    # ------------------------------------------------------------------ fuzzy mode
    fuzzy_pairs_idx = _fuzzy_global_matching(
        ref_list,
        cand_list,
        date_slop_days=max(0, int(args.date_slop_days)),
        amount_decimals=max(0, int(args.amount_decimals)),
    )
    ref_matched_idx = {a for a, _ in fuzzy_pairs_idx}
    cand_matched_idx = {b for _, b in fuzzy_pairs_idx}
    n_ref_rows = len(ref_list)
    n_cand_rows = len(cand_list)
    n_fuzzy = len(fuzzy_pairs_idx)
    ref_unmatched = set(range(n_ref_rows)) - ref_matched_idx
    cand_unmatched = set(range(n_cand_rows)) - cand_matched_idx

    fuzzy_cov = 100.0 * n_fuzzy / n_ref_rows if n_ref_rows else 0.0
    fuzzy_pairs_rows = [(ref_list[i], cand_list[j]) for i, j in fuzzy_pairs_idx]
    fstats = _aggregate_pair_stats(fuzzy_pairs_rows)
    fn = fstats["n"]
    fuzzy_class_pct = 100.0 * fstats["all_four"] / fn if fn else 0.0

    # How many fuzzy pairs are also same content_hash (sanity check).
    same_hash = sum(1 for i, j in fuzzy_pairs_idx if ref_list[i]["content_hash"] == cand_list[j]["content_hash"])

    print()
    print("--- Fuzzy match (account + direction + amount, date ± slop) ---")
    print(
        f"Rules: same (account_id, direction, amount rounded to {args.amount_decimals} dp); "
        f"pair if |txn_date_ref - txn_date_cand| ≤ {args.date_slop_days} day(s). "
        "One-to-one max matching inside each bucket."
    )
    print(f"Reference row count:   {n_ref_rows:,}")
    print(f"Candidate row count:   {n_cand_rows:,}")
    print(f"Fuzzy pairs formed:    {n_fuzzy:,}")
    print(f"Reference coverage:    {fuzzy_cov:.1f}% ({n_fuzzy:,} / {n_ref_rows:,} reference rows matched)")
    print(f"Candidate utilization: {_format_pct(n_fuzzy, n_cand_rows)} ({n_fuzzy:,} / {n_cand_rows:,} candidate rows matched)")
    print(f"Unmatched reference:   {len(ref_unmatched):,}")
    print(f"Unmatched candidate:   {len(cand_unmatched):,}")
    print(f"Pairs with same content_hash: {same_hash:,} ({_format_pct(same_hash, n_fuzzy)} of fuzzy pairs)")
    if ref_unmatched:
        rub = _breakdown_indices_by_source_type(ref_list, ref_unmatched)
        print(f"  Unmatched ref (source_type): {dict(rub)}")
    if cand_unmatched:
        cub = _breakdown_indices_by_source_type(cand_list, cand_unmatched)
        print(f"  Unmatched cand (source_type): {dict(cub)}")

    print()
    print("--- Classification agreement (fuzzy-matched pairs) ---")
    if fn:
        print(f"Fuzzy-matched pairs: {fn:,}")
        print(f"txn_type match:              {_format_pct(fstats['txn_type'], fn)} ({fstats['txn_type']:,} / {fn:,})")
        print(f"counterparty match (fuzzy): {_format_pct(fstats['counterparty'], fn)} ({fstats['counterparty']:,} / {fn:,})")
        print(
            f"counterparty_category match: {_format_pct(fstats['counterparty_category'], fn)} "
            f"({fstats['counterparty_category']:,} / {fn:,})"
        )
        print(f"spend_category match:        {_format_pct(fstats['spend_category'], fn)} ({fstats['spend_category']:,} / {fn:,})")
        print(f"All four (normalized) match: {fuzzy_class_pct:.1f}% ({fstats['all_four']:,} / {fn:,})")
    else:
        print("(no fuzzy pairs)")

    print()
    print("--- classification_source distribution ---")
    print(f"Reference:  {_format_dist(ref_src, len(ref_list))}")
    print(f"Candidate: {_format_dist(cand_src, len(cand_list))}")

    print()
    print("--- Summary ---")
    print(
        f"[Hash] Coverage {coverage_pct:.1f}% ({n_overlap:,} / {n_ref:,}) | "
        f"classification (all four): {class_match_pct:.1f}% | "
        f"candidate sources: {_format_dist(cand_src, len(cand_list))}"
    )
    print(
        f"[Fuzzy] Ref matched {fuzzy_cov:.1f}% ({n_fuzzy:,} / {n_ref_rows:,}) | "
        f"classification (all four on pairs): {fuzzy_class_pct:.1f}% | "
        f"date_slop={args.date_slop_days} amount_dp={args.amount_decimals}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
