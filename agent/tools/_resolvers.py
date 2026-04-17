"""
Shared helpers for agent tools (goal name resolution, etc.).

These are **not** LLM-exposed tools — only imported by other ``agent.tools`` modules.
"""

from __future__ import annotations

import re
from typing import Any

from httpx import AsyncClient


def _normalize_goal_query(q: str) -> str:
    """Lowercase and strip filler words so ``my house goal`` matches ``House down``."""
    s = q.strip().lower()
    for word in (" my ", " the ", " goal ", "  "):
        s = s.replace(word, " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


async def resolve_goal(client: AsyncClient, name_or_id: str) -> dict[str, Any] | None:
    """
    Resolve a user phrase or numeric id to one goal row (same shape as ``GET /api/goals``).

    Resolution order:
      1. If ``name_or_id`` parses as a positive integer, try exact ``id`` match.
      2. Else fetch **all** goals (no ``activation_status`` filter) and fuzzy-match on ``name``
         (case-insensitive substring, after light normalization).
      3. If several names match, prefer higher ``system_priority_score``, then shorter name
         (more specific substring wins).

    Returns ``None`` when nothing matches.
    """
    raw = (name_or_id or "").strip()
    if not raw:
        return None

    r = await client.get("/api/goals")
    r.raise_for_status()
    goals: list[dict[str, Any]] = r.json()
    if not goals:
        return None

    # 1) Numeric id
    if raw.isdigit():
        gid = int(raw)
        for g in goals:
            if g.get("id") == gid:
                return g

    # 2) Fuzzy name (dedupe by goal id)
    norm = _normalize_goal_query(raw)
    by_id: dict[int, dict[str, Any]] = {}
    for g in goals:
        gid = g.get("id")
        if gid is None:
            continue
        name = (g.get("name") or "").strip()
        if not name:
            continue
        lname = name.lower()
        hit = False
        if norm in lname or lname in norm:
            hit = True
        elif all(part in lname for part in norm.split() if len(part) > 2):
            hit = True
        if hit:
            by_id[int(gid)] = g

    if not by_id:
        for g in goals:
            gid = g.get("id")
            name = (g.get("name") or "").strip().lower()
            if gid is not None and norm and norm in name:
                by_id[int(gid)] = g

    if not by_id:
        return None

    matches = list(by_id.values())

    def score_row(g: dict[str, Any]) -> tuple[float, int, str]:
        pri = float(g.get("system_priority_score") or 0.0)
        n = str(g.get("name") or "")
        return (pri, -len(n), n)

    matches.sort(key=score_row, reverse=True)
    return matches[0]
