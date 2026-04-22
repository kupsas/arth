"""
Goal Graph Service — Phase B.1

Pure graph operations on the goal pyramid:
  - _load_graph:          Load all goals + links for a user into memory once,
                          build two adjacency dicts (children_map / parents_map).
  - validate_link:        Self-link guard + iterative DFS cycle detection.
  - get_goal_tree:        All goals grouped by tier + flat link list.
  - get_ancestors:        BFS upward (child → parent direction).
  - get_descendants:      BFS downward (parent → child direction).
  - get_impact:           BFS in both directions — every connected goal with
                          distance and link_type.
  - get_allocation_summary: SQL aggregation of monthly INR across ACTIVE goals.

This service does NOT compute goal progress (that is goal_evaluator.py), does NOT
evaluate activation conditions (that is activation_engine.py — B.2), and does NOT
define any API routes (that is B.3).

Edge direction convention
─────────────────────────
GoalLink stores parent_goal_id → child_goal_id, meaning the HIGHER tier goal
points to the LOWER tier goal (Vision → Strategy → Tactic → Operational).

  Ancestors   = traverse AGAINST edges: child_goal_id → parent_goal_id (upward)
  Descendants = traverse WITH edges:    parent_goal_id → child_goal_id (downward)

Cycle detection DFS therefore follows the WITH-edge direction (children_map).
If starting from the proposed child_id and following children_map we can reach
the proposed parent_id, the new edge would close a cycle.
"""

from __future__ import annotations

from collections import defaultdict, deque

from fastapi import HTTPException
from sqlmodel import Session, col, select

from api.models import Goal, GoalLink


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# L1–L4 replace legacy VISION/STRATEGY/TACTIC/OPERATIONAL (Goals architecture V2).
# Both spellings are accepted when bucketing; API normalises legacy → L* on write.
_TIER_TO_BUCKET: dict[str, str] = {
    "L1": "l1",
    "L2": "l2",
    "L3": "l3",
    "L4": "l4",
    "VISION": "l1",
    "STRATEGY": "l2",
    "TACTIC": "l3",
    "OPERATIONAL": "l4",
}

# Type aliases — purely for documentation, not enforced at runtime.
GoalsById   = dict[int, Goal]
LinksById   = list[GoalLink]
ChildrenMap = dict[int, list[GoalLink]]  # parent_goal_id → outgoing links  (down)
ParentsMap  = dict[int, list[GoalLink]]  # child_goal_id  → incoming links  (up)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _load_graph(
    session: Session,
    user_id: str,
) -> tuple[GoalsById, LinksById, ChildrenMap, ParentsMap]:
    """Load every goal and link for *user_id* into memory in two SQL queries.

    Returns four structures:
      goals_by_id  — {goal.id: Goal}  for O(1) lookups by id
      links        — flat list of all GoalLink rows (used by get_goal_tree)
      children_map — {parent_goal_id: [GoalLink, ...]}  follow edges downward
      parents_map  — {child_goal_id:  [GoalLink, ...]}  follow edges upward

    Orphaned links (where either endpoint's goal_id is absent from goals_by_id,
    which can only happen due to data inconsistency) are silently skipped so
    traversal functions never KeyError.
    """
    goals = session.exec(select(Goal).where(Goal.user_id == user_id)).all()
    links = session.exec(select(GoalLink).where(GoalLink.user_id == user_id)).all()

    goals_by_id: GoalsById = {g.id: g for g in goals if g.id is not None}

    children_map: ChildrenMap = defaultdict(list)
    parents_map:  ParentsMap  = defaultdict(list)

    valid_links: LinksById = []
    for link in links:
        # Skip any link whose endpoints are not in this user's goal set.
        if link.parent_goal_id in goals_by_id and link.child_goal_id in goals_by_id:
            children_map[link.parent_goal_id].append(link)
            parents_map[link.child_goal_id].append(link)
            valid_links.append(link)

    return goals_by_id, valid_links, children_map, parents_map


def _goal_to_dict(goal: Goal) -> dict:
    """Serialise a Goal to a JSON-safe dict (graph-service layer).

    This dict contains all goal fields but NO computed progress fields.
    The B.3 API layer enriches this with live progress from goal_evaluator.py.
    """
    return {
        "id": goal.id,
        "name": goal.name,
        "goal_type": goal.goal_type,
        # Hierarchy / pyramid fields (Phase B.0)
        "pyramid_id": goal.pyramid_id,
        "tier": goal.tier,
        "time_horizon": goal.time_horizon,
        "funding_mode": goal.funding_mode,
        "activation_status": goal.activation_status,
        "activation_condition": goal.activation_condition,
        "monthly_allocation": goal.monthly_allocation,
        "allocation_priority": goal.allocation_priority,
        "interruptible": goal.interruptible,
        "sensitivity_to_returns": goal.sensitivity_to_returns,
        # Goals architecture V2
        "goal_class": goal.goal_class,
        "recurrence_amount": goal.recurrence_amount,
        "recurrence_frequency": goal.recurrence_frequency,
        "recurrence_start": goal.recurrence_start.isoformat()
        if goal.recurrence_start
        else None,
        "recurrence_end": goal.recurrence_end.isoformat() if goal.recurrence_end else None,
        "goal_specific_inflation_rate": goal.goal_specific_inflation_rate,
        "expected_return_rate": goal.expected_return_rate,
        "starting_balance": goal.starting_balance,
        "system_priority_score": goal.system_priority_score,
        "goal_subtype": goal.goal_subtype,
        # Core goal fields
        "target_amount": goal.target_amount,
        "target_date": goal.target_date.isoformat() if goal.target_date else None,
        "target_metric": goal.target_metric,
        "priority": goal.priority,
        "linked_layer": goal.linked_layer,
        "linked_category": goal.linked_category,
        "chart_key": goal.chart_key,
        "progress_cadence": goal.progress_cadence,
        "current_value": goal.current_value,
        "notes": goal.notes,
        "user_id": goal.user_id,
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
        "updated_at": goal.updated_at.isoformat() if goal.updated_at else None,
    }


def _link_to_dict(link: GoalLink) -> dict:
    """Serialise a GoalLink to a JSON-safe dict."""
    return {
        "id": link.id,
        "parent_goal_id": link.parent_goal_id,
        "child_goal_id": link.child_goal_id,
        "link_type": link.link_type,
        "description": link.description,
        "contribution_amount": link.contribution_amount,
        "user_id": link.user_id,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_link(
    session: Session,
    parent_id: int,
    child_id: int,
    user_id: str,
) -> None:
    """Validate a proposed GoalLink *before* insertion.  Raises HTTPException on any issue.

    Checks (in order):
      1. Self-link:        parent_id must not equal child_id.
      2. Existence:        both goals must exist in the DB.
      3. Ownership:        both goals must belong to *user_id*.
      4. Cycle detection:  iterative DFS downward from child_id via children_map.
                           If parent_id is reachable, the new edge closes a cycle.

    Note: duplicate (parent, child, link_type) detection is NOT done here —
    the unique index «uq_goal_link_parent_child_type» in the DB handles that
    and will surface as an IntegrityError that the route layer should catch.
    """
    # 1. Self-link guard
    if parent_id == child_id:
        raise HTTPException(
            status_code=400,
            detail="A goal cannot link to itself (parent_id == child_id).",
        )

    # 2 + 3. Existence and ownership checks
    parent_goal = session.get(Goal, parent_id)
    if parent_goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {parent_id} not found.")
    if parent_goal.user_id != user_id:
        raise HTTPException(
            status_code=400,
            detail=f"Goal {parent_id} does not belong to the current user.",
        )

    child_goal = session.get(Goal, child_id)
    if child_goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {child_id} not found.")
    if child_goal.user_id != user_id:
        raise HTTPException(
            status_code=400,
            detail=f"Goal {child_id} does not belong to the current user.",
        )

    # 4. Cycle detection — iterative DFS following the WITH-edge direction.
    #
    # We want to know: "starting at child_id, if I keep following
    # parent→child edges (going further DOWN the pyramid), can I ever reach
    # parent_id?"  If yes, adding the edge parent_id→child_id would mean
    # parent_id is reachable from itself — a cycle.
    #
    # We load only children_map (we don't need the full graph here).
    _, _, children_map, _ = _load_graph(session, user_id)

    visited: set[int] = set()
    stack: list[int] = [child_id]

    while stack:
        node = stack.pop()
        if node == parent_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Adding link (goal {parent_id} → goal {child_id}) "
                    "would create a cycle in the goal graph."
                ),
            )
        if node in visited:
            continue
        visited.add(node)
        for link in children_map.get(node, []):
            stack.append(link.child_goal_id)


def get_goal_tree(session: Session, user_id: str) -> dict:
    """Return all goals grouped by tier, plus a flat list of all links.

    Response shape:
        {
            "l1":       [goal_dict, ...],
            "l2":       [goal_dict, ...],
            "l3":       [goal_dict, ...],
            "l4":       [goal_dict, ...],
            "untiered": [goal_dict, ...],   # tier None or unknown (not L1–L4 / legacy)
            "links":    [link_dict, ...],
        }

    Goals within each bucket are sorted by:
      1. allocation_priority ascending (lower number = higher priority; nulls last)
      2. pyramid_id alphabetically (e.g. V1 < V2)
      3. id ascending (stable tie-breaker)

    This function returns RAW goal data.  The B.3 tree route enriches each goal
    dict with computed progress before sending the response to the client.
    """
    goals_by_id, links, _, _ = _load_graph(session, user_id)

    buckets: dict[str, list[dict]] = {
        "l1": [],
        "l2": [],
        "l3": [],
        "l4": [],
        "untiered": [],
    }

    for goal in goals_by_id.values():
        tier_upper = (goal.tier or "").strip().upper()
        bucket_key = _TIER_TO_BUCKET.get(tier_upper, "untiered")
        buckets[bucket_key].append(_goal_to_dict(goal))

    # Sort each bucket for a stable, priority-ordered display.
    def _sort_key(g: dict) -> tuple:
        prio = g["allocation_priority"]
        return (
            prio if prio is not None else 999,   # nulls sort after any real priority
            g["pyramid_id"] or "",               # e.g. "V1" < "V2"
            g["id"] or 0,                        # numeric id as final tie-breaker
        )

    for bucket in buckets.values():
        bucket.sort(key=_sort_key)

    return {
        **buckets,
        "links": [_link_to_dict(link) for link in links],
    }


def get_ancestors(session: Session, goal_id: int, user_id: str) -> list[Goal]:
    """Return every ancestor of *goal_id* via BFS upward (child → parent direction).

    "Ancestor" means any goal reachable by following incoming edges (parents_map).
    This answers: "what higher-level goals does this goal ultimately serve?"

    Result ordering: BFS level order — immediate parents first, then grandparents.
    The source goal itself is never included in the result.

    Returns an empty list for root goals (Vision goals with no parents).
    Raises HTTPException 404 if goal_id is not found for this user.
    """
    goals_by_id, _, _, parents_map = _load_graph(session, user_id)

    if goal_id not in goals_by_id:
        raise HTTPException(
            status_code=404,
            detail=f"Goal {goal_id} not found for this user.",
        )

    # Seed visited with the source to prevent it appearing in the result.
    visited: set[int] = {goal_id}
    queue: deque[int] = deque([goal_id])
    result: list[Goal] = []

    while queue:
        current = queue.popleft()
        for link in parents_map.get(current, []):
            pid = link.parent_goal_id
            # Skip orphaned references and already-visited nodes.
            if pid not in visited and pid in goals_by_id:
                visited.add(pid)
                result.append(goals_by_id[pid])
                queue.append(pid)

    return result


def get_descendants(session: Session, goal_id: int, user_id: str) -> list[Goal]:
    """Return every descendant of *goal_id* via BFS downward (parent → child direction).

    "Descendant" means any goal reachable by following outgoing edges (children_map).
    This answers: "what lower-level actions flow from this goal?"

    Result ordering: BFS level order — immediate children first, then grandchildren.
    The source goal itself is never included in the result.

    Returns an empty list for leaf goals (Operational goals with no children).
    Raises HTTPException 404 if goal_id is not found for this user.
    """
    goals_by_id, _, children_map, _ = _load_graph(session, user_id)

    if goal_id not in goals_by_id:
        raise HTTPException(
            status_code=404,
            detail=f"Goal {goal_id} not found for this user.",
        )

    visited: set[int] = {goal_id}
    queue: deque[int] = deque([goal_id])
    result: list[Goal] = []

    while queue:
        current = queue.popleft()
        for link in children_map.get(current, []):
            cid = link.child_goal_id
            if cid not in visited and cid in goals_by_id:
                visited.add(cid)
                result.append(goals_by_id[cid])
                queue.append(cid)

    return result


def get_impact(session: Session, goal_id: int, user_id: str) -> list[dict]:
    """Return every goal connected to *goal_id* in either direction.

    Runs two independent BFS passes — one upward (ancestors) and one downward
    (descendants) — and merges the results.  This gives a complete picture of
    which goals are causally connected to the target, which is the input the
    simulation engine (Phase F4) needs.

    Each result entry:
        {
            "goal":      goal_dict,
            "direction": "ancestor" | "descendant",
            "distance":  int,   # hop count from goal_id (1 = direct neighbour)
            "link_type": str,   # link type of the FIRST edge that reached this node
        }

    When a goal is reachable via multiple paths, only the shortest path is
    included (BFS guarantees first discovery = shortest).

    Result is sorted by distance ascending, then ancestors before descendants
    at the same distance (so the immediately surrounding context appears first).

    Raises HTTPException 404 if goal_id is not found for this user.
    """
    goals_by_id, _, children_map, parents_map = _load_graph(session, user_id)

    if goal_id not in goals_by_id:
        raise HTTPException(
            status_code=404,
            detail=f"Goal {goal_id} not found for this user.",
        )

    result: list[dict] = []

    # ── Pass 1: BFS upward for ancestors ────────────────────────────────
    visited_up: set[int] = {goal_id}
    # Each queue item: (node_id, distance_from_source, link_type_that_got_us_here)
    up_queue: deque[tuple[int, int, str]] = deque()

    for link in parents_map.get(goal_id, []):
        pid = link.parent_goal_id
        if pid in goals_by_id and pid not in visited_up:
            visited_up.add(pid)
            up_queue.append((pid, 1, link.link_type))

    while up_queue:
        nid, dist, ltype = up_queue.popleft()
        result.append({
            "goal": _goal_to_dict(goals_by_id[nid]),
            "direction": "ancestor",
            "distance": dist,
            "link_type": ltype,
        })
        for link in parents_map.get(nid, []):
            pid = link.parent_goal_id
            if pid in goals_by_id and pid not in visited_up:
                visited_up.add(pid)
                up_queue.append((pid, dist + 1, link.link_type))

    # ── Pass 2: BFS downward for descendants ────────────────────────────
    visited_down: set[int] = {goal_id}
    down_queue: deque[tuple[int, int, str]] = deque()

    for link in children_map.get(goal_id, []):
        cid = link.child_goal_id
        if cid in goals_by_id and cid not in visited_down:
            visited_down.add(cid)
            down_queue.append((cid, 1, link.link_type))

    while down_queue:
        nid, dist, ltype = down_queue.popleft()
        result.append({
            "goal": _goal_to_dict(goals_by_id[nid]),
            "direction": "descendant",
            "distance": dist,
            "link_type": ltype,
        })
        for link in children_map.get(nid, []):
            cid = link.child_goal_id
            if cid in goals_by_id and cid not in visited_down:
                visited_down.add(cid)
                down_queue.append((cid, dist + 1, link.link_type))

    # Sort: nearest first; at equal distance, ancestors before descendants.
    result.sort(key=lambda r: (r["distance"], 0 if r["direction"] == "ancestor" else 1))

    return result


def get_allocation_summary(session: Session, user_id: str) -> dict:
    """Return the total monthly INR allocation committed to ACTIVE goals.

    Only goals with activation_status='ACTIVE' and a non-null monthly_allocation
    are included.  Results are sorted by allocation_priority (1 = highest priority
    receives first funding); goals with no priority are listed last.

    Response shape:
        {
            "total_allocated": float,   # sum of all monthly_allocation values
            "goals": [
                {
                    "id":                  int,
                    "name":                str,
                    "pyramid_id":          str | None,
                    "monthly_allocation":  float,
                    "allocation_priority": int | None,
                },
                ...
            ]
        }
    """
    query = (
        select(Goal)
        .where(Goal.user_id == user_id)
        .where(Goal.activation_status == "ACTIVE")
        # SQLAlchemy's isnot(None) compiles to "IS NOT NULL" in SQL.
        .where(col(Goal.monthly_allocation).isnot(None))
        .order_by(
            # col(...).is_(None) evaluates to 1 (True) for NULLs, 0 for non-NULLs.
            # Ordering by this expression first puts NULLs last.
            col(Goal.allocation_priority).is_(None),
            col(Goal.allocation_priority),
        )
    )
    active_goals = session.exec(query).all()

    # Build rows in a plain loop so static types stay precise (SQLModel columns are
    # loose unions; dict comprehensions + sum() confused mypy on this file).
    goal_rows: list[dict[str, int | str | float | None]] = []
    total_allocated = 0.0
    for g in active_goals:
        if g.monthly_allocation is None:
            continue
        ma = float(g.monthly_allocation)
        total_allocated += ma
        goal_rows.append(
            {
                "id": g.id,
                "name": g.name,
                "pyramid_id": g.pyramid_id,
                "monthly_allocation": ma,
                "allocation_priority": g.allocation_priority,
            }
        )

    return {
        "total_allocated": round(total_allocated, 2),
        "goals": goal_rows,
    }
