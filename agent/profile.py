"""
Dynamic user profile block for the system prompt (PII-stripped).
"""

from __future__ import annotations

import datetime as dt

from httpx import AsyncClient

from agent.sanitizer import sanitize_jsonable


async def generate_user_profile(client: AsyncClient) -> str:
    """
    Call a handful of read-only endpoints and format a compact markdown-ish summary.

    Steps:
      1. Holdings summary → net worth + allocation
      2. Metrics summary → this month's spending / savings rate
      3. Goals list (ACTIVE) + priorities (persist=false)
    """
    today = dt.datetime.now(dt.UTC).date().isoformat()
    parts: list[str] = [f"_Generated: {today} (UTC date)_", ""]

    # 1) Net worth snapshot
    try:
        h = await client.get("/api/holdings/summary")
        h.raise_for_status()
        hs = sanitize_jsonable(h.json())
        nw = hs.get("net_worth") or {}
        parts.append("### Net worth & allocation")
        parts.append(
            f"- **Total net worth (INR):** {nw.get('total')!s} "
            f"(assets {nw.get('assets')!s}, liabilities {nw.get('liabilities')!s})"
        )
        alloc = hs.get("allocation") or {}
        if isinstance(alloc, dict) and alloc:
            parts.append("- **Allocation (asset class → INR):**")
            for k, v in list(alloc.items())[:12]:
                parts.append(f"  - {k}: {v}")
        parts.append("")
    except Exception as e:
        parts.append(f"### Net worth\n- _(unavailable: {e})_\n")

    # 2) Current calendar month spending
    try:
        m = await client.get("/api/metrics/summary")
        m.raise_for_status()
        ms = sanitize_jsonable(m.json())
        parts.append("### Spending (selected period from API defaults)")
        parts.append(
            f"- **Window:** {ms.get('date_from')} → {ms.get('date_to')}\n"
            f"- **Income:** {ms.get('total_income')} | **Expense:** {ms.get('total_expense')} | "
            f"**Savings to investments:** {ms.get('total_savings')} | "
            f"**Savings rate %:** {ms.get('savings_rate')} | **Txns:** {ms.get('txn_count')}"
        )
        parts.append("")
    except Exception as e:
        parts.append(f"### Spending\n- _(unavailable: {e})_\n")

    # 3) Goals + priorities
    try:
        g = await client.get("/api/goals", params={"activation_status": "ACTIVE"})
        g.raise_for_status()
        goals = sanitize_jsonable(g.json())
        pr = await client.get("/api/goals/priorities", params={"persist": "false"})
        pr.raise_for_status()
        pri = sanitize_jsonable(pr.json())
        parts.append("### Active goals")
        if not goals:
            parts.append("- _(none returned)_")
        else:
            for row in goals[:8]:
                name = row.get("name", "?")
                pct = row.get("computed_percentage")
                stat = row.get("status")
                parts.append(f"- **{name}** — progress ~{pct!s}%, status {stat!s}")
        parts.append("")
        parts.append("### Priority scores (system)")
        for p in (pri.get("priorities") or [])[:8]:
            parts.append(
                f"- {p.get('goal_name')!s} — score {p.get('priority_score')!s}, "
                f"suggested rank {p.get('suggested_rank')!s}"
            )
        parts.append("")
    except Exception as e:
        parts.append(f"### Goals\n- _(unavailable: {e})_\n")

    parts.append(
        "_The user is referred to as “the user”; never echo raw account numbers or IDs._"
    )
    return "\n".join(parts)
