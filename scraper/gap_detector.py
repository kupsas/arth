"""
Coverage gap heuristics for onboarding (Track 2, Phase 4a).

``detect_gaps`` groups each user's transactions by source (``source_statement``) and
calendar month, then looks for *holes* in the time span where activity was first and
last seen.  Expectations follow ``expected_cadence`` from the merged bank-sender
config: monthly-style sources get month-level gaps; credit cards ignore short quiet
stretches (0 txns) unless the gap is longer than two full months, matching the plan.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import func
from sqlmodel import Session, col, select

from api.models import Transaction
from scraper.config_loader import BankSendersConfig

# InstaAlert Gmail searches use these slice sizes when building targeted windows
# (onboarding orchestrator — see :func:`compute_alert_backfill_windows`).
ALERT_BACKFILL_PRE_STATEMENT_DAYS = 90
ALERT_BACKFILL_UNCERTAIN_SLICE_DAYS = 90
# Cap ``coverage_uncertain`` slices when gap detection cannot infer months yet (no rows).
ALERT_BACKFILL_MAX_UNCERTAIN_WINDOWS = 48


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
    # Prefer stronger cadence when multiple senders map to the same source_key
    # (e.g. HDFC CC statements are ``monthly`` while InstaAlerts are ``per_transaction``).
    rank = {"monthly": 0, "quarterly": 1, "per_transaction": 2}

    def _cad_rank(ec: str) -> int:
        return rank.get((ec or "per_transaction").lower().strip(), 9)

    out: dict[str, dict[str, str]] = {}
    for _sender, blob in cfg.items():
        dname = (blob.get("display_name") or _sender) or "Unknown"
        st = (blob.get("source_type") or "savings").lower()
        ec = (blob.get("expected_cadence") or "per_transaction").lower()
        for _last4, acc in (blob.get("accounts") or {}).items():
            if not isinstance(acc, dict):
                continue
            sk = acc.get("source_key")
            if not isinstance(sk, str) or not sk:
                continue
            prev = out.get(sk)
            if prev is None or _cad_rank(ec) < _cad_rank(str(prev.get("expected_cadence") or "")):
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


def _last_day_of_month(y: int, m: int) -> dt.date:
    last = calendar.monthrange(y, m)[1]
    return dt.date(y, m, last)


def _ym_to_date_range(ym_start: str, ym_end: str) -> tuple[dt.date, dt.date]:
    y1, m1 = _parse_ym(ym_start)
    y2, m2 = _parse_ym(ym_end)
    d0 = dt.date(y1, m1, 1)
    d1 = _last_day_of_month(y2, m2)
    return d0, d1


def filter_onboarding_alert_ids_after_statements(
    session: Session,
    user_id: str,
    source_key: str,
    bank: BankSendersConfig,
    alert_items: list[dict[str, str]],
    *,
    had_statement_ids_at_init: bool,
) -> list[str]:
    """After statement-phase import, decide which InstaAlert / alert emails still need parsing.

    ``alert_items`` entries must include ``id`` (Gmail message id) and ``received_at``
    (ISO-8601 string).  Returns message ids **oldest first**.

    When **no statement-cadence senders** are configured for this ``source_key`` (no annual /
    monthly / quarterly senders in bank config), every alert id is returned — we cannot
    infer month-level coverage from statement PDFs.

    When statement senders **are** configured and statements ran first and month gaps are
    empty, returns ``[]`` (statement PDFs are treated as source-of-truth; redundant alert
    volume is skipped).

    When gaps exist, only alerts whose *received* date falls inside a gap month window
    are kept (parallel fill for holes).
    """
    if not alert_items:
        return []

    def _sort_key(row: dict[str, str]) -> tuple[int, str]:
        raw = row.get("received_at") or ""
        try:
            d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return (int(d.timestamp()), row.get("id") or "")
        except ValueError:
            return (0, row.get("id") or "")

    ordered = sorted(alert_items, key=_sort_key)

    if not had_statement_ids_at_init:
        return [str(r["id"]) for r in ordered if r.get("id")]

    reports = detect_gaps(session, user_id, bank)
    rep = next((r for r in reports if str(r.get("source") or "") == source_key), None)

    if rep is None:
        return [str(r["id"]) for r in ordered if r.get("id")]

    gaps = rep.get("gaps") or []
    note = str(rep.get("note") or "").lower()
    ec = str(rep.get("expected_cadence") or "").lower()

    if not gaps:
        if "not enough month coverage" in note:
            return [str(r["id"]) for r in ordered if r.get("id")]
        if "sporadic" in note or ec == "per_transaction":
            return [str(r["id"]) for r in ordered if r.get("id")]
        return []

    windows: list[tuple[dt.date, dt.date]] = []
    for g in gaps:
        if not isinstance(g, dict):
            continue
        ps = g.get("period_start")
        pe = g.get("period_end")
        if isinstance(ps, str) and isinstance(pe, str) and ps.strip() and pe.strip():
            windows.append(_ym_to_date_range(ps.strip(), pe.strip()))

    if not windows:
        return [str(r["id"]) for r in ordered if r.get("id")]

    def _in_windows(rec: dt.date) -> bool:
        for a, b in windows:
            if a <= rec <= b:
                return True
        return False

    out: list[str] = []
    for r in ordered:
        mid = r.get("id")
        if not mid:
            continue
        raw = r.get("received_at") or ""
        try:
            rdt = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if _in_windows(rdt):
            out.append(str(mid))
    return out


def _earliest_txn_date_for_source(
    session: Session,
    user_id: str,
    source_key: str,
) -> dt.date | None:
    """Oldest transaction date for this pipeline ``source_key`` (any ``source_type``).

    Used to anchor **pre-statement** InstaAlert windows: statement PDFs parsed from email
    still store ``source_type='email'``, so this date is the start of “known good”
    statement-backed coverage; alerts *before* this day may be the only history.
    """
    q = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.source_statement == source_key)
        .where(col(Transaction.txn_date).is_not(None))
        .order_by(col(Transaction.txn_date).asc())
        .limit(1)
    )
    row = session.exec(q).first()
    if row is None:
        return None
    return row.txn_date


def _slice_range_into_windows(
    start_inclusive: dt.date,
    end_exclusive: dt.date,
    *,
    max_days: int,
    kind: str,
    label_prefix: str,
) -> list[dict[str, Any]]:
    """Split ``[start_inclusive, end_exclusive)`` into consecutive Gmail-sized windows."""
    out: list[dict[str, Any]] = []
    cur = start_inclusive
    while cur < end_exclusive:
        nxt = min(cur + dt.timedelta(days=max_days), end_exclusive)
        if cur < nxt:
            out.append(
                {
                    "after": cur.isoformat(),
                    "before": nxt.isoformat(),
                    "kind": kind,
                    "label": f"{label_prefix}: {cur.isoformat()} .. {nxt.isoformat()}",
                }
            )
        cur = nxt
    return out


def compute_alert_backfill_windows(
    session: Session,
    user_id: str,
    source_key: str,
    bank: BankSendersConfig,
    *,
    gmail_after_inclusive: dt.date,
    gmail_before_exclusive: dt.date,
    had_statement_ids_at_init: bool,
) -> list[dict[str, Any]]:
    """Plan small Gmail date windows for InstaAlert import after statement PDFs ran.

    **Order**
      1. **Gap** windows — months :func:`detect_gaps` marks as missing coverage (statement
         is source of truth; alerts only fill holes).
      2. **Pre-statement** window — up to :data:`ALERT_BACKFILL_PRE_STATEMENT_DAYS` before the
         oldest stored transaction (alerts may be the only data before statements began).
      3. **Coverage-uncertain** slices — only when we cannot trust gap detection (no report
         yet, or “not enough month coverage”, or sporadic ``per_transaction`` metadata).

    Each returned dict is JSON-serialisable::

        {"after": "YYYY-MM-DD", "before": "YYYY-MM-DD", "kind": str, "label": str}

    ``after`` is Gmail-inclusive; ``before`` is Gmail-**exclusive** (matches
    ``GmailClient.search_messages`` query semantics).

    When (2) and (3) both apply, gap windows still come first; uncertain slices are a
    fallback so onboarding never fires one giant unbounded InstaAlert search.
    """
    windows: list[dict[str, Any]] = []
    reports = detect_gaps(session, user_id, bank)
    rep = next((r for r in reports if str(r.get("source") or "") == source_key), None)

    if rep:
        for g in rep.get("gaps") or []:
            if not isinstance(g, dict):
                continue
            ps = g.get("period_start")
            pe = g.get("period_end")
            if not (isinstance(ps, str) and isinstance(pe, str) and ps.strip() and pe.strip()):
                continue
            d0, d1 = _ym_to_date_range(ps.strip(), pe.strip())
            before_ex = d1 + dt.timedelta(days=1)
            before_ex = min(before_ex, gmail_before_exclusive)
            d0 = max(d0, gmail_after_inclusive)
            if d0 < before_ex:
                windows.append(
                    {
                        "after": d0.isoformat(),
                        "before": before_ex.isoformat(),
                        "kind": "gap",
                        "label": f"Gap fill: {ps.strip()}..{pe.strip()}",
                    }
                )

    earliest = _earliest_txn_date_for_source(session, user_id, source_key)
    if earliest:
        end_ex = earliest
        start = end_ex - dt.timedelta(days=ALERT_BACKFILL_PRE_STATEMENT_DAYS)
        start = max(start, gmail_after_inclusive)
        if start < end_ex:
            windows.append(
                {
                    "after": start.isoformat(),
                    "before": end_ex.isoformat(),
                    "kind": "pre_statement",
                    "label": f"Backfill before statements: {start.isoformat()} .. {end_ex.isoformat()}",
                }
            )

    if not had_statement_ids_at_init:
        return windows

    note = str((rep or {}).get("note") or "").lower()
    ec = str((rep or {}).get("expected_cadence") or "").lower()
    gaps = (rep or {}).get("gaps") or []

    need_uncertain = False
    if rep is None:
        need_uncertain = True
    elif not gaps:
        if "not enough month coverage" in note:
            need_uncertain = True
        elif "sporadic" in note or ec == "per_transaction":
            need_uncertain = True

    if need_uncertain:
        uncertain = _slice_range_into_windows(
            gmail_after_inclusive,
            gmail_before_exclusive,
            max_days=ALERT_BACKFILL_UNCERTAIN_SLICE_DAYS,
            kind="coverage_uncertain",
            label_prefix="Alert import (coverage uncertain)",
        )
        windows.extend(uncertain[:ALERT_BACKFILL_MAX_UNCERTAIN_WINDOWS])

    return windows


def list_transaction_sources(
    session: Session,
    user_id: str,
) -> list[str]:
    """Distinct ``source_statement`` values for a user (lightweight)."""
    stmt = select(Transaction.source_statement).where(
        Transaction.user_id == user_id, Transaction.source_statement != ""
    ).distinct()
    return [str(x) for x in session.exec(stmt) if x]
