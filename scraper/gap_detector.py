"""
Coverage gap heuristics for onboarding (Track 2, Phase 4a).

``detect_gaps`` groups each user's transactions by source (``source_statement``) and
calendar month, then looks for *holes* in the time span where activity was first and
last seen.  Expectations follow ``expected_cadence`` from the merged bank-sender
config: monthly-style sources get month-level gaps; credit cards ignore short quiet
stretches (0 txns) unless the gap is longer than two full months, matching the plan.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import func
from sqlmodel import Session, select

from api.models import Transaction
from scraper.config_loader import BankSendersConfig


# ── month helpers ────────────────────────────────────────────────────────────


def _month_add(y: int, m: int, delta: int) -> tuple[int, int]:
    """Add ``delta`` calendar months to (y, m) (1-12)."""
    idx = y * 12 + (m - 1) + delta
    ny, rest = divmod(idx, 12)
    return ny, rest + 1


def _ym_key(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def _parse_ym(ym: str) -> tuple[int, int]:
    a, b = ym.split("-", 1)
    return int(a), int(b)


def _iter_months_inclusive(ym_start: str, ym_end: str) -> list[str]:
    y1, m1 = _parse_ym(ym_start)
    y2, m2 = _parse_ym(ym_end)
    if (y1, m1) > (y2, m2):
        return []
    out: list[str] = []
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        out.append(_ym_key(y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _merge_consecutive_months(chunks: Iterable[str]) -> list[dict[str, str]]:
    """Turn ['2021-01','2021-02'] into a single range label for the UI."""
    months = sorted(set(chunks))
    if not months:
        return []

    def key(m: str) -> tuple[int, int]:
        return _parse_ym(m)

    runs: list[list[str]] = [[months[0]]]
    for m in months[1:]:
        py, pm = _parse_ym(runs[-1][-1])
        ny, nm = _parse_ym(m)
        # consecutive calendar month
        nxt_y, nxt_m = _month_add(py, pm, 1)
        if (ny, nm) == (nxt_y, nxt_m):
            runs[-1].append(m)
        else:
            runs.append([m])

    merged: list[dict[str, str]] = []
    for r in runs:
        start, end = r[0], r[-1]
        if start == end:
            label = start
        else:
            label = f"{start}..{end}"
        merged.append({"period_start": start, "period_end": end, "label": label})
    return merged


# ── source metadata from config ───────────────────────────────────────────────


def _build_source_key_meta(cfg: BankSendersConfig) -> dict[str, dict[str, str]]:
    """Map ``source_key`` (pipeline key) to display + cadence metadata."""
    out: dict[str, dict[str, str]] = {}
    for _sender, blob in cfg.items():
        dname = (blob.get("display_name") or _sender) or "Unknown"
        st = (blob.get("source_type") or "savings").lower()
        ec = (blob.get("expected_cadence") or "per_transaction").lower()
        for _last4, acc in (blob.get("accounts") or {}).items():
            if not isinstance(acc, dict):
                continue
            sk = acc.get("source_key")
            if isinstance(sk, str) and sk and sk not in out:
                out[sk] = {
                    "display_name": str(dname),
                    "source_type": st,
                    "expected_cadence": ec,
                }
    return out


# ── core gap rules ───────────────────────────────────────────────────────────


def _candidates_zero_months(
    active_months: set[str],
    range_months: list[str],
    *,
    source_type: str,
    is_credit: bool,
) -> list[str]:
    """Return month keys that are considered a 'gap' in ``range_months``."""
    zeroes = [ym for ym in range_months if ym not in active_months]
    if not zeroes:
        return []

    if is_credit or source_type == "credit_card":
        # Credit cards may be quiet for 1-2 months — flag only 3+ consecutive.
        return _long_streaks_only(zeroes, min_len=3)

    # Other savings / monthly PDF sources: any empty month in range is a gap.
    return zeroes


def _long_streaks_only(sorted_zero_months: list[str], *, min_len: int) -> list[str]:
    if not sorted_zero_months:
        return []
    s = sorted(set(sorted_zero_months), key=_parse_ym)
    if min_len < 1:
        return s

    runs: list[list[str]] = [[s[0]]]
    for m in s[1:]:
        py, pm = _parse_ym(runs[-1][-1])
        ny, nm = _parse_ym(m)
        nxt_y, nxt_m = _month_add(py, pm, 1)
        if (ny, nm) == (nxt_y, nxt_m):
            runs[-1].append(m)
        else:
            runs.append([m])

    out: list[str] = []
    for r in runs:
        if len(r) >= min_len:
            out.extend(r)
    return out


# ── quarterly (loose) ───────────────────────────────────────────────────────
# A calendar quarter is "empty" if every in-range month of that quarter has 0 txns.


def _quarterly_zero_months(
    range_months: list[str],
    active_months: set[str],
) -> list[str]:
    """Months that sit in a calendar quarter where *every* in-range month had zero txns."""
    from collections import defaultdict

    by_q: dict[str, list[str]] = defaultdict(list)
    for ym in range_months:
        y, m = _parse_ym(ym)
        q = (m - 1) // 3 + 1
        by_q[f"{y}-Q{q}"].append(ym)
    out: list[str] = []
    for mlist in by_q.values():
        if mlist and all(x not in active_months for x in mlist):
            out.extend(mlist)
    return out


# ── public entry ────────────────────────────────────────────────────────────


@dataclass
class _PerSource:
    min_ym: str
    max_ym: str
    counts: dict[str, int] = field(default_factory=dict)  # ym -> txn count
    source_label: str = ""
    source_type: str = "savings"
    expected_cadence: str = "per_transaction"
    total_count: int = 0


def _collect_per_source(
    session: Session,
    user_id: str,
) -> dict[str, _PerSource]:
    # One grouped query: source_statement x YYYY-MM x count
    yms = func.strftime("%Y-%m", Transaction.txn_date).label("ym")
    stmt = (
        select(
            Transaction.source_statement,
            yms,
            func.count(),  # COUNT(*) per group
        )
        .where(Transaction.user_id == user_id)
        .group_by(Transaction.source_statement, yms)
    )
    per: dict[str, _PerSource] = {}
    for sk, ym, c in session.exec(stmt):
        if not sk or not ym:
            continue
        key = str(sk)
        if key not in per:
            per[key] = _PerSource(min_ym=str(ym), max_ym=str(ym))
        p = per[key]
        p.counts[str(ym)] = int(c)
        p.total_count += int(c)

    for p in per.values():
        if p.counts:
            keys = sorted(p.counts.keys(), key=_parse_ym)
            p.min_ym, p.max_ym = keys[0], keys[-1]
    return per


def detect_gaps(
    session: Session,
    user_id: str,
    source_configs: BankSendersConfig,
) -> list[dict[str, Any]]:
    """
    Return a list of gap reports, one per ``source_statement`` the user has data for.

    Sources with ``per_transaction`` cadence produce no month gaps.  Sources with
    no transactions are omitted.  A single month of data yields an empty ``gaps``
    list (nothing to compare in-between).
    """
    meta = _build_source_key_meta(source_configs)
    by_src = _collect_per_source(session, user_id)
    if not by_src:
        return []

    reports: list[dict[str, Any]] = []
    for source_key, ps in sorted(by_src.items(), key=lambda kv: kv[0]):
        m = meta.get(source_key) or {}
        label = m.get("display_name") or source_key
        st = (m.get("source_type") or "savings").lower()
        ec = (m.get("expected_cadence") or "per_transaction").lower()
        ps.source_label = label
        ps.source_type = st
        ps.expected_cadence = ec

        y0, m0 = _parse_ym(ps.min_ym)
        y1, m1 = _parse_ym(ps.max_ym)
        d0 = dt.date(y0, m0, 1)
        d1 = dt.date(y1, m1, 1)

        if ec == "per_transaction":
            reports.append(
                {
                    "source": source_key,
                    "source_label": label,
                    "source_type": st,
                    "expected_cadence": ec,
                    "date_range_start": d0.isoformat(),
                    "date_range_end": d1.isoformat(),
                    "transaction_count": ps.total_count,
                    "gaps": [],
                    "note": "Sporadic or alert-only source — not checked month-by-month.",
                }
            )
            continue

        rmonths = _iter_months_inclusive(ps.min_ym, ps.max_ym)
        active = {k for k, n in ps.counts.items() if n > 0}
        is_cc = st == "credit_card"

        if len(rmonths) < 2:
            reports.append(
                {
                    "source": source_key,
                    "source_label": label,
                    "source_type": st,
                    "expected_cadence": ec,
                    "date_range_start": d0.isoformat(),
                    "date_range_end": d1.isoformat(),
                    "transaction_count": ps.total_count,
                    "gaps": [],
                    "note": "Not enough month coverage to infer gaps yet.",
                }
            )
            continue

        if ec == "quarterly":
            zq = _quarterly_zero_months(rmonths, active)
            raw_zero = set(zq)
            if is_cc:
                raw_zero = set(_long_streaks_only(list(raw_zero), min_len=3))
        else:
            raw = _candidates_zero_months(active, rmonths, source_type=st, is_credit=is_cc)
            raw_zero = set(raw)

        # Merge consecutive for cleaner UI
        pieces = _merge_consecutive_months(list(raw_zero))
        gaps: list[dict[str, str]] = []
        for p in pieces:
            gaps.append(
                {
                    "kind": "missing_coverage",
                    "period_label": p["label"],
                    "period_start": p["period_start"],
                    "period_end": p["period_end"],
                    "reason": _gap_reason(st, ec, is_cc, p["label"]),
                }
            )

        reports.append(
            {
                "source": source_key,
                "source_label": label,
                "source_type": st,
                "expected_cadence": ec,
                "date_range_start": d0.isoformat(),
                "date_range_end": d1.isoformat(),
                "transaction_count": ps.total_count,
                "gaps": gaps,
            }
        )

    return reports


def _gap_reason(
    source_type: str, cadence: str, is_cc: bool, label: str
) -> str:
    if is_cc:
        return (
            f"No card activity for 3+ consecutive months ({label}). "
            "If you did use the card, try uploading that month's statement."
        )
    if cadence == "quarterly":
        return (
            f"Possible missing quarterly statement in {label} — consider uploading "
            "the e-statement if you have it."
        )
    return (
        f"No transactions parsed for {label}. "
        "If the account was active, upload a statement to fill the hole."
    )


def list_transaction_sources(
    session: Session,
    user_id: str,
) -> list[str]:
    """Distinct ``source_statement`` values for a user (lightweight)."""
    stmt = select(Transaction.source_statement).where(
        Transaction.user_id == user_id, Transaction.source_statement != ""
    ).distinct()
    return [str(x) for x in session.exec(stmt) if x]
