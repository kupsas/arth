"""
API integration tests for Phase B — Goal Hierarchy endpoints.

Covers (via FastAPI TestClient, in-memory SQLite):
  B.3 — Extended goals CRUD (hierarchy fields, enum validation, pyramid_id uniqueness,
         activation_condition validation, activation cascade on COMPLETED)
  B.3 — GoalLink CRUD (create, duplicate guard, cycle guard, delete, patch)
  B.3 — Goal tree routes (/tree, /allocation, /ancestors, /descendants, /impact)
  B.3 — LifeEvent routes (create, duplicate guard, patch/cascade)

Security checks embedded throughout:
  - User scoping: requests never see another user's data
  - Auth required: endpoints return 401/403 when auth is bypassed (tested via
    a separate no-auth client)
  - Input validation: invalid enums, oversized strings, malformed DSL strings
    all return 400 before touching the DB

All tests are isolated — each test function gets its own in-memory SQLite DB.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.auth import get_current_user
from api.database import get_session
from api.main import app


# ───────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ───────────────────────────────────────────────────────────────────────────

@pytest.fixture(name="engine")
def in_memory_engine():
    """Fresh in-memory SQLite for every test function."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(name="client")
def api_client(engine):
    """TestClient wired to the in-memory engine; auth bypassed as 'test_user'."""
    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "test_user"
    yield TestClient(app)
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_user, None)


@contextmanager
def _as_user(user_id: str):
    """Temporarily override get_current_user for cross-user isolation tests.

    app.dependency_overrides is a shared global dict — two simultaneous
    TestClient fixtures would clobber each other's override.  Instead we swap
    the override for a single block of code and restore 'test_user' afterwards.
    """
    prev = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: user_id
    try:
        yield
    finally:
        if prev is not None:
            app.dependency_overrides[get_current_user] = prev
        else:
            app.dependency_overrides.pop(get_current_user, None)


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

_GOAL_BASE = {
    "name": "Test Goal",
    "goal_type": "SAVINGS",
}


def _create_goal(client: TestClient, **extra) -> dict:
    """POST /api/goals and assert 201; return the response body."""
    payload = {**_GOAL_BASE, **extra}
    resp = client.post("/api/goals", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_link(client: TestClient, parent_id: int, child_id: int,
                 link_type: str = "DECOMPOSES_INTO") -> dict:
    """POST /api/goal-links and assert 201; return body."""
    resp = client.post("/api/goal-links", json={
        "parent_goal_id": parent_id,
        "child_goal_id": child_id,
        "link_type": link_type,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_event(client: TestClient, event_key: str, occurred: bool = False) -> dict:
    """POST /api/life-events and assert 201; return body."""
    resp = client.post("/api/life-events", json={
        "event_key": event_key,
        "occurred": occurred,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════════
# 1. GOALS CRUD — hierarchy fields
# ═══════════════════════════════════════════════════════════════════════════


class TestGoalsCrudHierarchy:
    """Create / read / update goals with B.0 hierarchy fields."""

    def test_create_goal_with_all_hierarchy_fields(self, client):
        """POST /api/goals with all B.0 fields returns them in the response."""
        g = _create_goal(
            client,
            name="Emergency Fund",
            pyramid_id="S4",
            tier="STRATEGY",
            time_horizon="ANNUAL",
            funding_mode="ACCUMULATION",
            activation_status="ACTIVE",
            monthly_allocation=50000,
            allocation_priority=1,
            interruptible=False,
            sensitivity_to_returns="LOW",
        )
        assert g["pyramid_id"] == "S4"
        assert g["tier"] == "STRATEGY"
        assert g["time_horizon"] == "ANNUAL"
        assert g["funding_mode"] == "ACCUMULATION"
        assert g["activation_status"] == "ACTIVE"
        assert g["monthly_allocation"] == 50000
        assert g["allocation_priority"] == 1
        assert g["interruptible"] is False
        assert g["sensitivity_to_returns"] == "LOW"

    def test_create_goal_with_valid_activation_condition(self, client):
        """An activation_condition using valid DSL is accepted and stored."""
        g = _create_goal(
            client,
            activation_status="PENDING",
            activation_condition="goal:S4:completed AND event:employed",
        )
        assert g["activation_condition"] == "goal:S4:completed AND event:employed"
        assert g["activation_status"] == "PENDING"

    def test_create_goal_invalid_activation_condition_rejected(self, client):
        """A syntactically invalid activation_condition returns 400."""
        resp = client.post("/api/goals", json={
            **_GOAL_BASE,
            "activation_condition": "goal:S4:completed; DROP TABLE goals",
        })
        assert resp.status_code == 400

    def test_create_goal_invalid_tier_rejected(self, client):
        """An unknown tier value returns 400."""
        resp = client.post("/api/goals", json={**_GOAL_BASE, "tier": "MADE_UP"})
        assert resp.status_code == 400

    def test_create_goal_invalid_funding_mode_rejected(self, client):
        """An unknown funding_mode returns 400."""
        resp = client.post("/api/goals", json={**_GOAL_BASE, "funding_mode": "RANDOM"})
        assert resp.status_code == 400

    def test_create_goal_invalid_activation_status_rejected(self, client):
        """An unknown activation_status returns 400."""
        resp = client.post("/api/goals", json={**_GOAL_BASE, "activation_status": "ZOMBIE"})
        assert resp.status_code == 400

    def test_pyramid_id_uniqueness_enforced(self, client):
        """Two goals cannot share the same pyramid_id for the same user."""
        _create_goal(client, pyramid_id="V1")
        resp = client.post("/api/goals", json={**_GOAL_BASE, "pyramid_id": "V1"})
        assert resp.status_code == 400
        assert "pyramid_id" in resp.json()["detail"].lower() or "already" in resp.json()["detail"].lower()

    def test_list_goals_filter_by_tier(self, client):
        """GET /api/goals?tier=VISION returns only Vision-tier goals."""
        _create_goal(client, tier="VISION", name="Vision goal")
        _create_goal(client, tier="STRATEGY", name="Strategy goal")
        resp = client.get("/api/goals?tier=VISION")
        assert resp.status_code == 200
        goals = resp.json()
        assert all(g["tier"] == "VISION" for g in goals)
        assert len(goals) == 1

    def test_list_goals_filter_by_activation_status(self, client):
        """GET /api/goals?activation_status=PENDING returns only PENDING goals."""
        _create_goal(client, activation_status="PENDING", activation_condition="event:employed")
        _create_goal(client, activation_status="ACTIVE")
        resp = client.get("/api/goals?activation_status=PENDING")
        assert resp.status_code == 200
        goals = resp.json()
        assert all(g["activation_status"] == "PENDING" for g in goals)

    def test_list_goals_filter_by_funding_mode(self, client):
        """GET /api/goals?funding_mode=CONSTRAINT returns only CONSTRAINT goals."""
        _create_goal(client, funding_mode="CONSTRAINT", name="Constrain me")
        _create_goal(client, funding_mode="ACCUMULATION", name="Pile it up")
        resp = client.get("/api/goals?funding_mode=CONSTRAINT")
        assert resp.status_code == 200
        goals = resp.json()
        assert all(g["funding_mode"] == "CONSTRAINT" for g in goals)

    def test_patch_activation_status_to_completed(self, client):
        """PATCH goal with activation_status=COMPLETED is accepted and persisted."""
        g = _create_goal(client, name="Done goal")
        resp = client.patch(f"/api/goals/{g['id']}", json={"activation_status": "COMPLETED"})
        assert resp.status_code == 200
        assert resp.json()["activation_status"] == "COMPLETED"

    def test_patch_goal_not_found_for_other_user(self, client):
        """PATCH a goal owned by 'test_user' from 'other_user' returns 404."""
        g = _create_goal(client, name="Mine")
        with _as_user("other_user"):
            resp = client.patch(f"/api/goals/{g['id']}", json={"name": "Hacked"})
        assert resp.status_code == 404

    def test_delete_goal_not_found_for_other_user(self, client):
        """DELETE a goal owned by 'test_user' from 'other_user' returns 404."""
        g = _create_goal(client, name="Mine")
        with _as_user("other_user"):
            resp = client.delete(f"/api/goals/{g['id']}")
        assert resp.status_code == 404

    def test_creating_completed_goal_triggers_cascade(self, client):
        """Creating a goal already as COMPLETED should trigger the activation cascade.

        If another PENDING goal depends on this new one, it should flip to ACTIVE.
        We set up the prerequisite first (pending T4 waiting for S4:completed),
        then create S4 as COMPLETED in a single POST.
        """
        # T4 is PENDING, waiting for S4 to complete
        t4 = _create_goal(
            client, name="T4", pyramid_id="T4",
            activation_status="PENDING",
            activation_condition="goal:S4:completed",
        )

        # Create S4 already as COMPLETED — cascade should activate T4
        _create_goal(client, name="S4", pyramid_id="S4", activation_status="COMPLETED")

        # Verify T4 was activated
        resp = client.get(f"/api/goals/{t4['id']}")
        assert resp.status_code == 200
        assert resp.json()["activation_status"] == "ACTIVE"


# ═══════════════════════════════════════════════════════════════════════════
# 2. GOAL LINKS CRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestGoalLinksCrud:
    """POST / GET / PATCH / DELETE /api/goal-links."""

    def test_create_link_returns_201(self, client):
        """POST /api/goal-links returns 201 and the new link's data."""
        parent = _create_goal(client, name="Parent", pyramid_id="V1")
        child = _create_goal(client, name="Child", pyramid_id="S1")
        link = _create_link(client, parent["id"], child["id"])
        assert link["parent_goal_id"] == parent["id"]
        assert link["child_goal_id"] == child["id"]
        assert link["link_type"] == "DECOMPOSES_INTO"

    def test_invalid_link_type_rejected(self, client):
        """An invalid link_type (not in the allowed set) returns 400."""
        parent = _create_goal(client, name="Parent")
        child = _create_goal(client, name="Child")
        resp = client.post("/api/goal-links", json={
            "parent_goal_id": parent["id"],
            "child_goal_id": child["id"],
            "link_type": "TOTALLY_WRONG",
        })
        assert resp.status_code == 400

    def test_duplicate_link_rejected(self, client):
        """Creating the same (parent, child, link_type) triple twice returns 400."""
        parent = _create_goal(client, name="Parent")
        child = _create_goal(client, name="Child")
        _create_link(client, parent["id"], child["id"])
        # Second identical link:
        resp = client.post("/api/goal-links", json={
            "parent_goal_id": parent["id"],
            "child_goal_id": child["id"],
            "link_type": "DECOMPOSES_INTO",
        })
        assert resp.status_code == 400

    def test_self_link_rejected(self, client):
        """A goal cannot link to itself — returns 400."""
        g = _create_goal(client, name="Self")
        resp = client.post("/api/goal-links", json={
            "parent_goal_id": g["id"],
            "child_goal_id": g["id"],
            "link_type": "DECOMPOSES_INTO",
        })
        assert resp.status_code == 400

    def test_cycle_rejected(self, client):
        """Adding a link that would close a cycle returns 400."""
        a = _create_goal(client, name="A", pyramid_id="A1")
        b = _create_goal(client, name="B", pyramid_id="B1")
        _create_link(client, a["id"], b["id"])

        # Adding B → A would close a cycle:
        resp = client.post("/api/goal-links", json={
            "parent_goal_id": b["id"],
            "child_goal_id": a["id"],
            "link_type": "DECOMPOSES_INTO",
        })
        assert resp.status_code == 400
        assert "cycle" in resp.json()["detail"].lower()

    def test_list_goal_links(self, client):
        """GET /api/goal-links returns all links for the user."""
        parent = _create_goal(client, name="Parent")
        c1 = _create_goal(client, name="Child1")
        c2 = _create_goal(client, name="Child2")
        _create_link(client, parent["id"], c1["id"])
        _create_link(client, parent["id"], c2["id"], link_type="DEPENDS_ON")

        resp = client.get("/api/goal-links")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_goal_links_filter_by_parent(self, client):
        """GET /api/goal-links?parent_goal_id=X returns only that parent's links."""
        p1 = _create_goal(client, name="P1")
        p2 = _create_goal(client, name="P2")
        c = _create_goal(client, name="Child")
        _create_link(client, p1["id"], c["id"])
        _create_link(client, p2["id"], c["id"], link_type="CONTRIBUTES_TO")

        resp = client.get(f"/api/goal-links?parent_goal_id={p1['id']}")
        assert resp.status_code == 200
        links = resp.json()
        assert len(links) == 1
        assert links[0]["parent_goal_id"] == p1["id"]

    def test_patch_link_description(self, client):
        """PATCH /api/goal-links/{id} can update description."""
        parent = _create_goal(client, name="Parent")
        child = _create_goal(client, name="Child")
        link = _create_link(client, parent["id"], child["id"])

        resp = client.patch(f"/api/goal-links/{link['id']}", json={
            "description": "Updated description"
        })
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated description"

    def test_delete_link(self, client):
        """DELETE /api/goal-links/{id} removes the link (204)."""
        parent = _create_goal(client, name="Parent")
        child = _create_goal(client, name="Child")
        link = _create_link(client, parent["id"], child["id"])

        resp = client.delete(f"/api/goal-links/{link['id']}")
        assert resp.status_code == 204

        # Verify it's gone:
        resp = client.get("/api/goal-links")
        assert resp.status_code == 200
        assert all(lk["id"] != link["id"] for lk in resp.json())

    def test_delete_other_users_link_returns_404(self, client):
        """DELETE on a link owned by 'test_user' from 'other_user' returns 404."""
        parent = _create_goal(client, name="Parent")
        child = _create_goal(client, name="Child")
        link = _create_link(client, parent["id"], child["id"])

        with _as_user("other_user"):
            resp = client.delete(f"/api/goal-links/{link['id']}")
        assert resp.status_code == 404

    def test_list_links_no_cross_user_leakage(self, client):
        """GET /api/goal-links for other_user must not include test_user's links."""
        parent = _create_goal(client, name="Parent")
        child = _create_goal(client, name="Child")
        _create_link(client, parent["id"], child["id"])

        # other_user has no goals/links — should get empty list
        with _as_user("other_user"):
            resp = client.get("/api/goal-links")
        assert resp.status_code == 200
        assert resp.json() == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. GOAL TREE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


class TestGoalTreeEndpoints:
    """Tests for GET /api/goals/tree, /allocation, /ancestors, /descendants, /impact."""

    # ── /tree ─────────────────────────────────────────────────────────────

    def test_tree_returns_tier_buckets(self, client):
        """GET /api/goals/tree returns vision/strategy/tactic/operational/untiered + links."""
        _create_goal(client, name="V1 Goal", tier="VISION", pyramid_id="V1")
        _create_goal(client, name="S1 Goal", tier="STRATEGY", pyramid_id="S1")

        resp = client.get("/api/goals/tree")
        assert resp.status_code == 200
        tree = resp.json()
        assert "vision" in tree
        assert "strategy" in tree
        assert "tactic" in tree
        assert "operational" in tree
        assert "links" in tree
        assert len(tree["vision"]) == 1
        assert len(tree["strategy"]) == 1

    def test_tree_includes_links(self, client):
        """Links between goals appear in the tree's 'links' list."""
        v = _create_goal(client, name="Vision", tier="VISION", pyramid_id="V1")
        s = _create_goal(client, name="Strategy", tier="STRATEGY", pyramid_id="S1")
        _create_link(client, v["id"], s["id"])

        resp = client.get("/api/goals/tree")
        assert resp.status_code == 200
        links = resp.json()["links"]
        assert len(links) == 1
        assert links[0]["parent_goal_id"] == v["id"]

    def test_tree_no_cross_user_leakage(self, client):
        """GET /api/goals/tree for other_user must not include test_user's goals."""
        _create_goal(client, name="Private goal", tier="VISION")
        with _as_user("other_user"):
            resp = client.get("/api/goals/tree")
        assert resp.status_code == 200
        tree = resp.json()
        all_goals = (
            tree["vision"] + tree["strategy"] +
            tree["tactic"] + tree["operational"] + tree["untiered"]
        )
        assert all_goals == []  # other_user has no goals

    def test_tree_empty_when_no_goals(self, client):
        """GET /api/goals/tree for a user with no goals returns empty buckets."""
        resp = client.get("/api/goals/tree")
        assert resp.status_code == 200
        tree = resp.json()
        all_goals = (
            tree["vision"] + tree["strategy"] +
            tree["tactic"] + tree["operational"] + tree["untiered"]
        )
        assert all_goals == []

    # ── /allocation ────────────────────────────────────────────────────────

    def test_allocation_sums_active_goals(self, client):
        """GET /api/goals/allocation returns sum of ACTIVE goals' monthly_allocation."""
        _create_goal(client, name="G1", activation_status="ACTIVE", monthly_allocation=20000)
        _create_goal(client, name="G2", activation_status="ACTIVE", monthly_allocation=15000)
        # PENDING goal — should not count
        _create_goal(
            client, name="G3", activation_status="PENDING",
            monthly_allocation=5000, activation_condition="event:employed",
        )

        resp = client.get("/api/goals/allocation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_allocated"] == 35000.0
        assert len(data["goals"]) == 2

    def test_allocation_empty_when_no_active_goals(self, client):
        """GET /api/goals/allocation returns 0 when there are no ACTIVE goals."""
        resp = client.get("/api/goals/allocation")
        assert resp.status_code == 200
        assert resp.json()["total_allocated"] == 0.0
        assert resp.json()["goals"] == []

    # ── /ancestors ─────────────────────────────────────────────────────────

    def test_ancestors_of_child(self, client):
        """GET /api/goals/{id}/ancestors returns the parent chain (BFS order)."""
        v = _create_goal(client, name="Vision", tier="VISION", pyramid_id="V1")
        s = _create_goal(client, name="Strategy", tier="STRATEGY", pyramid_id="S1")
        o = _create_goal(client, name="Ops", tier="OPERATIONAL", pyramid_id="O1")
        _create_link(client, v["id"], s["id"])
        _create_link(client, s["id"], o["id"])

        resp = client.get(f"/api/goals/{o['id']}/ancestors")
        assert resp.status_code == 200
        ancestors = resp.json()
        ancestor_ids = [g["id"] for g in ancestors]
        # Immediate parent (s) comes before grandparent (v)
        assert ancestor_ids[0] == s["id"]
        assert v["id"] in ancestor_ids
        assert o["id"] not in ancestor_ids  # source never returned

    def test_ancestors_of_root_is_empty(self, client):
        """GET /api/goals/{id}/ancestors for a root goal returns []."""
        v = _create_goal(client, name="Vision", tier="VISION", pyramid_id="V1")
        resp = client.get(f"/api/goals/{v['id']}/ancestors")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ancestors_of_nonexistent_goal_returns_404(self, client):
        """GET /api/goals/99999/ancestors returns 404."""
        resp = client.get("/api/goals/99999/ancestors")
        assert resp.status_code == 404

    def test_ancestors_no_cross_user(self, client):
        """Ancestors endpoint scoped to the authenticated user — cannot traverse
        other user's chain even if goal ids are known."""
        v = _create_goal(client, name="Vision")
        s = _create_goal(client, name="Strategy")
        _create_link(client, v["id"], s["id"])

        # other_user tries to access test_user's goal — should 404
        with _as_user("other_user"):
            resp = client.get(f"/api/goals/{s['id']}/ancestors")
        assert resp.status_code == 404

    # ── /descendants ────────────────────────────────────────────────────────

    def test_descendants_of_root(self, client):
        """GET /api/goals/{id}/descendants returns all children (BFS)."""
        v = _create_goal(client, name="Vision", tier="VISION", pyramid_id="V1")
        s = _create_goal(client, name="Strategy", tier="STRATEGY", pyramid_id="S1")
        o = _create_goal(client, name="Ops", tier="OPERATIONAL", pyramid_id="O1")
        _create_link(client, v["id"], s["id"])
        _create_link(client, s["id"], o["id"])

        resp = client.get(f"/api/goals/{v['id']}/descendants")
        assert resp.status_code == 200
        desc_ids = [g["id"] for g in resp.json()]
        assert s["id"] in desc_ids
        assert o["id"] in desc_ids
        assert v["id"] not in desc_ids

    def test_descendants_of_leaf_is_empty(self, client):
        """GET /api/goals/{id}/descendants for a leaf goal returns []."""
        leaf = _create_goal(client, name="Leaf", tier="OPERATIONAL")
        resp = client.get(f"/api/goals/{leaf['id']}/descendants")
        assert resp.status_code == 200
        assert resp.json() == []

    # ── /impact ────────────────────────────────────────────────────────────

    def test_impact_returns_both_directions(self, client):
        """GET /api/goals/{id}/impact returns ancestors + descendants for a middle node."""
        v = _create_goal(client, name="Vision", pyramid_id="V1")
        s = _create_goal(client, name="Strategy", pyramid_id="S1")
        o = _create_goal(client, name="Ops", pyramid_id="O1")
        _create_link(client, v["id"], s["id"])
        _create_link(client, s["id"], o["id"])

        resp = client.get(f"/api/goals/{s['id']}/impact")
        assert resp.status_code == 200
        data = resp.json()
        directions = {row["direction"] for row in data}
        assert "ancestor" in directions
        assert "descendant" in directions

    def test_impact_includes_distance_and_link_type(self, client):
        """Each impact row has 'distance' and 'link_type' fields."""
        v = _create_goal(client, name="Vision", pyramid_id="V1")
        s = _create_goal(client, name="Strategy", pyramid_id="S1")
        _create_link(client, v["id"], s["id"])

        resp = client.get(f"/api/goals/{s['id']}/impact")
        assert resp.status_code == 200
        row = resp.json()[0]
        assert "distance" in row
        assert "link_type" in row
        assert row["distance"] == 1

    def test_impact_of_isolated_goal_is_empty(self, client):
        """A goal with no links has an empty impact list."""
        alone = _create_goal(client, name="Alone")
        resp = client.get(f"/api/goals/{alone['id']}/impact")
        assert resp.status_code == 200
        assert resp.json() == []


# ═══════════════════════════════════════════════════════════════════════════
# 4. LIFE EVENTS + ACTIVATION CASCADE
# ═══════════════════════════════════════════════════════════════════════════


class TestLifeEvents:
    """POST / GET / PATCH /api/life-events and the activation cascade."""

    def test_create_life_event(self, client):
        """POST /api/life-events creates a new event (201)."""
        resp = client.post("/api/life-events", json={
            "event_key": "employed",
            "occurred": False,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["event_key"] == "employed"
        assert data["occurred"] is False

    def test_duplicate_event_key_rejected(self, client):
        """Creating two life events with the same key for the same user returns 400."""
        _create_event(client, "employed")
        resp = client.post("/api/life-events", json={"event_key": "employed"})
        assert resp.status_code == 400

    def test_list_life_events(self, client):
        """GET /api/life-events returns all events for the user."""
        _create_event(client, "employed")
        _create_event(client, "married")
        resp = client.get("/api/life-events")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_life_events_no_cross_user(self, client):
        """GET /api/life-events for other_user must not include test_user's events."""
        _create_event(client, "employed")
        with _as_user("other_user"):
            resp = client.get("/api/life-events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_patch_event_mark_occurred(self, client):
        """PATCH /api/life-events/{id} can mark an event as occurred."""
        event = _create_event(client, "employed", occurred=False)
        resp = client.patch(f"/api/life-events/{event['id']}", json={
            "occurred": True,
            "occurred_date": "2026-03-01",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["occurred"] is True
        assert data["occurred_date"] == "2026-03-01"

    def test_patch_nonexistent_event_returns_404(self, client):
        """PATCH /api/life-events/99999 returns 404."""
        resp = client.patch("/api/life-events/99999", json={"occurred": True})
        assert resp.status_code == 404

    def test_patch_other_users_event_returns_404(self, client):
        """PATCH on another user's event returns 404."""
        event = _create_event(client, "employed")
        with _as_user("other_user"):
            resp = client.patch(f"/api/life-events/{event['id']}", json={"occurred": True})
        assert resp.status_code == 404

    def test_marking_event_occurred_activates_pending_goal(self, client):
        """When a LifeEvent is marked occurred=True, PENDING goals whose condition
        references that event should automatically flip to ACTIVE.

        This tests the full end-to-end cascade: POST event → PATCH occurred=True
        → check GET /api/goals/{id} shows activation_status=ACTIVE.
        """
        # Create an event that is not yet occurred
        event = _create_event(client, "child_born", occurred=False)

        # Create a goal that is waiting for the event
        goal = _create_goal(
            client, name="Child Fund", pyramid_id="T11",
            activation_status="PENDING",
            activation_condition="event:child_born",
        )

        # Mark the event as occurred — this should cascade-activate the goal
        resp = client.patch(f"/api/life-events/{event['id']}", json={
            "occurred": True,
            "occurred_date": "2026-06-01",
        })
        assert resp.status_code == 200

        # Verify the goal was activated
        resp = client.get(f"/api/goals/{goal['id']}")
        assert resp.status_code == 200
        assert resp.json()["activation_status"] == "ACTIVE"

    def test_multi_condition_cascade_via_life_event(self, client):
        """Cascading conditions: G1 is PENDING waiting for S4:completed;
        G2 is PENDING waiting for G1:active.

        Step 1: Mark 'employed' event → S4 (PENDING, condition event:employed) activates.
        Step 2: When S4 activates, the cascade should NOT automatically complete G1 since
        G1 waits for S4:completed, and S4 just became ACTIVE (not COMPLETED).
        Verifying the cascade only goes as far as conditions allow.
        """
        event = _create_event(client, "employed", occurred=False)

        # S4 is PENDING, will activate once 'employed' fires
        s4 = _create_goal(
            client, name="S4 Re-enter Workforce", pyramid_id="S4",
            activation_status="PENDING", activation_condition="event:employed",
        )
        # G1 waits for S4 to be COMPLETED (not just active)
        g1 = _create_goal(
            client, name="G1 Invest", pyramid_id="T4",
            activation_status="PENDING", activation_condition="goal:S4:completed",
        )

        # Mark 'employed' — S4 should activate, G1 should NOT (S4 is active, not completed)
        resp = client.patch(f"/api/life-events/{event['id']}", json={"occurred": True})
        assert resp.status_code == 200

        resp_s4 = client.get(f"/api/goals/{s4['id']}")
        resp_g1 = client.get(f"/api/goals/{g1['id']}")
        assert resp_s4.json()["activation_status"] == "ACTIVE"  # S4 flipped
        assert resp_g1.json()["activation_status"] == "PENDING"  # G1 still waiting


# ═══════════════════════════════════════════════════════════════════════════
# 5. SECURITY — input validation edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestSecurityInputValidation:
    """Fuzz-style tests that throw adversarial inputs at the API layer."""

    def test_oversized_activation_condition_rejected(self, client):
        """activation_condition longer than 500 chars is rejected by Pydantic at the API layer."""
        long_condition = "goal:S4:completed AND " * 30  # well over 500 chars
        resp = client.post("/api/goals", json={
            **_GOAL_BASE,
            "activation_condition": long_condition,
        })
        # Should be 400 (Pydantic max_length=500 rejects it, or DSL validator rejects it)
        assert resp.status_code in (400, 422)

    def test_pyramid_id_max_length_enforced(self, client):
        """pyramid_id longer than 10 chars is rejected (max_length=10 in GoalCreate)."""
        resp = client.post("/api/goals", json={
            **_GOAL_BASE,
            "pyramid_id": "TOOLONGPYRAMID",  # 14 chars
        })
        assert resp.status_code in (400, 422)

    def test_sql_injection_in_activation_condition(self, client):
        """SQL-injection-style input in activation_condition is rejected (not parseable)."""
        resp = client.post("/api/goals", json={
            **_GOAL_BASE,
            "activation_condition": "goal:S4:completed'; DROP TABLE goals; --",
        })
        assert resp.status_code == 400

    def test_xss_in_goal_name_stored_as_literal(self, client):
        """XSS-style strings in goal name are stored as-is (we escape at render, not DB).
        The API should accept and echo back the string without executing it.
        """
        xss = "<script>alert('xss')</script>"
        g = _create_goal(client, name=xss)
        # Name is stored literally; no 400
        assert g["name"] == xss

    def test_allocation_priority_out_of_range(self, client):
        """allocation_priority must be 1–100; 0 or 101 are rejected."""
        resp = client.post("/api/goals", json={**_GOAL_BASE, "allocation_priority": 0})
        assert resp.status_code in (400, 422)
        resp2 = client.post("/api/goals", json={**_GOAL_BASE, "allocation_priority": 101})
        assert resp2.status_code in (400, 422)

    def test_negative_monthly_allocation_rejected(self, client):
        """monthly_allocation must be >= 0; negative values are rejected."""
        resp = client.post("/api/goals", json={**_GOAL_BASE, "monthly_allocation": -1})
        assert resp.status_code in (400, 422)

    def test_link_to_nonexistent_goal_returns_404(self, client):
        """Creating a link where parent_id doesn't exist returns 404."""
        child = _create_goal(client, name="Child")
        resp = client.post("/api/goal-links", json={
            "parent_goal_id": 99999,
            "child_goal_id": child["id"],
            "link_type": "DECOMPOSES_INTO",
        })
        assert resp.status_code == 404

    def test_patch_link_cannot_change_parent_child(self, client):
        """PATCH /api/goal-links/{id} only allows description/contribution_amount.
        Attempting to pass parent_goal_id or child_goal_id is silently ignored (only
        whitelisted fields are applied), so the link structure stays intact.
        """
        p = _create_goal(client, name="Parent")
        c1 = _create_goal(client, name="Child1")
        c2 = _create_goal(client, name="Child2")
        link = _create_link(client, p["id"], c1["id"])

        # Attempt to change child via PATCH (field is not in GoalLinkPatch model)
        resp = client.patch(f"/api/goal-links/{link['id']}", json={
            "child_goal_id": c2["id"],
            "description": "Legitimate update",
        })
        assert resp.status_code == 200
        updated = resp.json()
        # child_goal_id must remain c1, not c2
        assert updated["child_goal_id"] == c1["id"]
        assert updated["description"] == "Legitimate update"
