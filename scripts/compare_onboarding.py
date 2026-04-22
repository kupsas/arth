#!/usr/bin/env python3
"""
Compare a **reference** SQLite database (typically a frozen ``arth.db`` or
``arth_reference.db``) against a **candidate** produced by onboarding (e.g.
``arth_onboarding.db``).

Rows are aligned on ``content_hash`` (the canonical dedupe key for bank
:class:`~api.models.Transaction` rows). The script prints coverage, hashes
present only on one side, and classification agreement on the overlap.

**Typical usage** (from repo root)::

    # After copying a baseline:  cp data/arth.db data/arth_reference.db
    python3 scripts/compare_onboarding.py \\
        --reference data/arth_reference.db \\
        --candidate data/arth_onboarding.db

Defaults point at ``data/arth.db`` vs ``data/arth_onboarding.db`` so a quick
check works once those files exist.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
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


def _load_txn_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Pull one row per ``content_hash`` from ``transactions``."""
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT content_hash, account_id, txn_date, txn_type, counterparty,
               counterparty_category, spend_category, classification_source, source_type
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


def _compare_overlap(ref_rows: dict[str, sqlite3.Row], cand_rows: dict[str, sqlite3.Row], overlap: set[str]) -> dict[str, Any]:
    """How often candidate fields match reference on shared hashes."""
    n = len(overlap)
    if n == 0:
        return {
            "n": 0,
            "txn_type": 0,
            "counterparty": 0,
            "counterparty_category": 0,
            "spend_category": 0,
        }
    m_tt = m_cp = m_cc = m_sc = 0
    for h in overlap:
        rr = ref_rows[h]
        cr = cand_rows[h]
        if _norm_optional_str(rr["txn_type"]) == _norm_optional_str(cr["txn_type"]):
            m_tt += 1
        if _normalize_counterparty(rr["counterparty"]) == _normalize_counterparty(cr["counterparty"]):
            m_cp += 1
        if _norm_optional_str(rr["counterparty_category"]) == _norm_optional_str(cr["counterparty_category"]):
            m_cc += 1
        if _norm_optional_str(rr["spend_category"]) == _norm_optional_str(cr["spend_category"]):
            m_sc += 1
    return {"n": n, "txn_type": m_tt, "counterparty": m_cp, "counterparty_category": m_cc, "spend_category": m_sc}


def _breakdown_by_source_type(rows: list[sqlite3.Row], hashes: set[str]) -> Counter[str]:
    c: Counter[str] = Counter()
    for r in rows:
        if r["content_hash"] in hashes:
            st = r["source_type"] or "(null)"
            c[st] += 1
    return c


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        type=Path,
        default=REPO_ROOT / "data" / "arth.db",
        help="Reference / gold SQLite file (default: data/arth.db).",
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        default=REPO_ROOT / "data" / "arth_onboarding.db",
        help="Candidate DB from onboarding (default: data/arth_onboarding.db).",
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

    stats = _compare_overlap(ref_by_hash, cand_by_hash, overlap)
    n_stats = stats["n"]
    # One “all four fields match” rate for the headline (strict on normalized fields).
    if n_stats:
        all_four = sum(
            1
            for h in overlap
            if (
                _norm_optional_str(ref_by_hash[h]["txn_type"]) == _norm_optional_str(cand_by_hash[h]["txn_type"])
                and _normalize_counterparty(ref_by_hash[h]["counterparty"])
                == _normalize_counterparty(cand_by_hash[h]["counterparty"])
                and _norm_optional_str(ref_by_hash[h]["counterparty_category"])
                == _norm_optional_str(cand_by_hash[h]["counterparty_category"])
                and _norm_optional_str(ref_by_hash[h]["spend_category"])
                == _norm_optional_str(cand_by_hash[h]["spend_category"])
            )
        )
        class_match_pct = 100.0 * all_four / n_stats
    else:
        all_four = 0
        class_match_pct = 0.0

    ref_src = _source_distribution(ref_list)
    cand_src = _source_distribution(cand_list)

    print("=== Onboarding Quality Report ===")
    print(f"Reference:  {ref_path}")
    print(f"Candidate: {cand_path}")
    print()
    print("--- Transaction coverage ---")
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
        print(f"All four (normalized) match: {class_match_pct:.1f}% ({all_four:,} / {n_stats:,})")
    else:
        print("(no overlapping hashes — cannot compare classification)")

    print()
    print("--- classification_source distribution ---")
    print(f"Reference:  {_format_dist(ref_src, n_ref)}")
    print(f"Candidate: {_format_dist(cand_src, n_cand)}")

    # Compact one-liner similar to the implementation plan example.
    print()
    print("--- Summary ---")
    print(
        f"Coverage {coverage_pct:.1f}% ({n_overlap:,} / {n_ref:,}) | "
        f"classification (all four on overlap): {class_match_pct:.1f}% "
        f"(txn_type {_format_pct(stats['txn_type'], n_stats)}, "
        f"counterparty {_format_pct(stats['counterparty'], n_stats)}, "
        f"category {_format_pct(stats['counterparty_category'], n_stats)}) | "
        f"candidate sources: {_format_dist(cand_src, n_cand)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
