"""
API integration tests for Phase B — Goal Hierarchy endpoints.

Covers (via FastAPI TestClient, in-memory SQLite):
  B.3 — Extended goals CRUD (hierarchy fields, enum validation, pyramid_id uniqueness,
         activation_condition validation, activation cascade on COMPLETED)
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


@pytest.fixture(name="bare_client")
def api_client_no_auth(engine):
    """TestClient with the real get_current_user dependency (no override).

    Session still points to the in-memory DB so the app boots cleanly, but
    no arth_session cookie is sent — every request should return 401.
    """
    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    # Deliberately do NOT override get_current_user — the real implementation
    # reads the cookie and raises 401 when it is absent.
    app.dependency_overrides.pop(get_current_user, None)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_session, None)


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
        assert g["tier"] == "L2"  # STRATEGY normalised to L2 on write
        assert g["time_horizon"] == "ANNUAL"
        assert g["funding_mode"] == "ACCUMULATION"
        assert g["activation_status"] == "ACTIVE"
        assert g["monthly_allocation"] == 50000
        assert g["allocation_priority"] == 1
        assert g["interruptible"] is False
        assert g["sensitivity_to_returns"] == "LOW"

    def test_create_goal_with_v2_simulation_fields(self, client):
        """POST /api/goals persists goal_class, recurrence, inflation, subtype (Sub-Plan A)."""
        g = _create_goal(
            client,
            name="Loan EMI",
            goal_type="DEBT_PAYOFF",
            goal_class="RECURRING_CASH_FLOW",
            recurrence_amount=55000,
            recurrence_frequency="MONTHLY",
            recurrence_start="2026-05-01",
            goal_specific_inflation_rate=6.0,
            expected_return_rate=10.0,
            starting_balance=10000,
            goal_subtype="LOAN_PAYOFF",
        )
        assert g["goal_class"] == "RECURRING_CASH_FLOW"
        assert g["recurrence_amount"] == 55000
        assert g["recurrence_frequency"] == "MONTHLY"
        assert g["recurrence_start"] == "2026-05-01"
        assert g["goal_specific_inflation_rate"] == 6.0
        assert g["expected_return_rate"] == 10.0
        assert g["starting_balance"] == 10000
        assert g["goal_subtype"] == "LOAN_PAYOFF"
        assert g["system_priority_score"] is None

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

    def test_create_goal_retired_paused_activation_rejected(self, client):
        """PAUSED was removed; funding gaps show in progress % instead."""
        resp = client.post("/api/goals", json={**_GOAL_BASE, "activation_status": "PAUSED"})
        assert resp.status_code == 400

    def test_second_investment_goal_uses_unlinked_chart_key(self, client):
        """Only one INVESTMENT goal may use investment_net; the next is stored with chart_key None."""
        r1 = client.post(
            "/api/goals",
            json={
                "name": "First investment",
                "goal_type": "INVESTMENT",
                "target_amount": 100000,
                "chart_key": "investment_net",
            },
        )
        assert r1.status_code == 201, r1.text
        assert r1.json()["chart_key"] == "investment_net"

        r2 = client.post(
            "/api/goals",
            json={
                "name": "House — down payment",
                "goal_type": "INVESTMENT",
                "target_amount": 500000,
                "chart_key": "investment_net",
            },
        )
        assert r2.status_code == 201, r2.text
        assert r2.json()["chart_key"] is None

    def test_pyramid_id_uniqueness_enforced(self, client):
        """Two goals cannot share the same pyramid_id for the same user."""
        _create_goal(client, pyramid_id="V1")
        resp = client.post("/api/goals", json={**_GOAL_BASE, "pyramid_id": "V1"})
        assert resp.status_code == 400
        assert "pyramid_id" in resp.json()["detail"].lower() or "already" in resp.json()["detail"].lower()

    def test_list_goals_filter_by_tier(self, client):
        """GET /api/goals?tier=L1 returns only L1-tier goals."""
        _create_goal(client, tier="L1", name="Top goal")
        _create_goal(client, tier="L2", name="Next goal")
        resp = client.get("/api/goals?tier=L1")
        assert resp.status_code == 200
        goals = resp.json()
        assert all(g["tier"] == "L1" for g in goals)
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
# 2. LIFE EVENTS + ACTIVATION CASCADE
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


# ═══════════════════════════════════════════════════════════════════════════
# 6. AUTH GUARD (B.6.4) — every B.3 endpoint requires authentication
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthGuard:
    """Verify that all Phase B.3 endpoints return HTTP 401 when no session
    cookie is present.

    The ``bare_client`` fixture uses the real ``get_current_user`` dependency
    (not overridden), so any request without an ``arth_session`` cookie hits
    the 401 guard in ``api/auth.py``.

    This satisfies the B.6.4 checklist item: "all new endpoints require auth".
    """

    # ── Goals hierarchy endpoints ─────────────────────────────────────────

    def test_goals_list_requires_auth(self, bare_client):
        assert bare_client.get("/api/goals").status_code == 401

    def test_goals_create_requires_auth(self, bare_client):
        assert bare_client.post("/api/goals", json={
            "name": "X", "goal_type": "SAVINGS",
        }).status_code == 401

    def test_goals_get_requires_auth(self, bare_client):
        assert bare_client.get("/api/goals/1").status_code == 401

    def test_goals_patch_requires_auth(self, bare_client):
        assert bare_client.patch("/api/goals/1", json={"name": "X"}).status_code == 401

    def test_goals_delete_requires_auth(self, bare_client):
        assert bare_client.delete("/api/goals/1").status_code == 401

    # ── LifeEvent endpoints ────────────────────────────────────────────────

    def test_life_events_list_requires_auth(self, bare_client):
        assert bare_client.get("/api/life-events").status_code == 401

    def test_life_events_create_requires_auth(self, bare_client):
        assert bare_client.post("/api/life-events", json={
            "event_key": "employed",
        }).status_code == 401

    def test_life_events_patch_requires_auth(self, bare_client):
        assert bare_client.patch("/api/life-events/1", json={
            "occurred": True,
        }).status_code == 401
