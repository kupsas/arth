"""
Unit tests for Phase B — Goal Hierarchy services.

Covers:
  B.2 — Activation condition engine (parser, validator, evaluator, cascade hook)
  B.1 — Goal graph service (tree, ancestors, descendants, impact, allocation, cycle detection)

All tests use an in-memory SQLite DB via SQLModel + StaticPool so they are fast,
isolated, and never touch real data.

What we test here vs. test_goal_hierarchy_api.py:
  - THIS file exercises services directly (no HTTP layer) — pure unit/integration of
    the Python functions.
  - The API file exercises the FastAPI endpoints end-to-end via TestClient.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.models import Goal, GoalLink, LifeEvent
from api.services.activation_engine import (
    AndCondition,
    ConditionParseError,
    EventCondition,
    GoalCondition,
    OrCondition,
    check_and_update_activations,
    evaluate_condition,
    parse_condition,
    validate_condition,
)
from api.services.goal_graph import (
    get_allocation_summary,
    get_ancestors,
    get_descendants,
    get_goal_tree,
    get_impact,
    validate_link,
)


# ───────────────────────────────────────────────────────────────────────────
# Shared DB fixtures
# ───────────────────────────────────────────────────────────────────────────

@pytest.fixture(name="engine")
def in_memory_engine():
    """Fresh in-memory SQLite for every test function.

    StaticPool is critical: without it, sqlite:// gives a *new* in-memory DB to
    each connection, so our data disappears between the Session write and the
    service read.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(name="session")
def db_session(engine):
    """Open a single Session tied to the in-memory engine."""
    with Session(engine) as sess:
        yield sess


# ───────────────────────────────────────────────────────────────────────────
# Helper — quick goal factory
# ───────────────────────────────────────────────────────────────────────────

def _make_goal(
    session: Session,
    *,
    name: str,
    pyramid_id: str | None = None,
    tier: str | None = None,
    activation_status: str = "ACTIVE",
    activation_condition: str | None = None,
    monthly_allocation: float | None = None,
    allocation_priority: int | None = None,
    user_id: str = "tester",
) -> Goal:
    """Insert a minimal Goal row and flush so it gets an id."""
    goal = Goal(
        name=name,
        goal_type="SAVINGS",
        pyramid_id=pyramid_id,
        tier=tier,
        activation_status=activation_status,
        activation_condition=activation_condition,
        monthly_allocation=monthly_allocation,
        allocation_priority=allocation_priority,
        user_id=user_id,
    )
    session.add(goal)
    session.flush()  # assigns auto-increment id without committing
    return goal


def _make_link(
    session: Session,
    *,
    parent: Goal,
    child: Goal,
    link_type: str = "DECOMPOSES_INTO",
    user_id: str = "tester",
) -> GoalLink:
    """Insert a GoalLink and flush."""
    link = GoalLink(
        parent_goal_id=parent.id,
        child_goal_id=child.id,
        link_type=link_type,
        user_id=user_id,
    )
    session.add(link)
    session.flush()
    return link


def _make_event(
    session: Session,
    *,
    event_key: str,
    occurred: bool = False,
    user_id: str = "tester",
) -> LifeEvent:
    """Insert a LifeEvent row and flush."""
    event = LifeEvent(
        event_key=event_key,
        occurred=occurred,
        user_id=user_id,
    )
    session.add(event)
    session.flush()
    return event


# ═══════════════════════════════════════════════════════════════════════════
# 1. ACTIVATION ENGINE — Parser
# ═══════════════════════════════════════════════════════════════════════════


class TestParser:
    """Tests for parse_condition() — the DSL string → AST step."""

    # ── Valid atoms ────────────────────────────────────────────────────────

    def test_parse_single_goal_atom(self):
        """A bare goal atom parses to a GoalCondition with lowercased status."""
        node = parse_condition("goal:S4:completed")
        assert isinstance(node, GoalCondition)
        assert node.pyramid_id == "S4"
        assert node.status == "completed"  # always lowercased

    def test_parse_single_event_atom(self):
        """A bare event atom parses to an EventCondition."""
        node = parse_condition("event:employed")
        assert isinstance(node, EventCondition)
        assert node.event_key == "employed"

    def test_parse_goal_atom_status_lowercased(self):
        """Status part of a goal atom is stored lowercased regardless of input."""
        node = parse_condition("goal:T3:COMPLETED")
        assert isinstance(node, GoalCondition)
        assert node.status == "completed"

    # ── AND / OR combinators ───────────────────────────────────────────────

    def test_parse_and(self):
        """Two atoms joined by AND produce an AndCondition with both as children."""
        node = parse_condition("goal:S4:completed AND goal:S6:completed")
        assert isinstance(node, AndCondition)
        assert len(node.children) == 2
        assert all(isinstance(c, GoalCondition) for c in node.children)

    def test_parse_or(self):
        """Two atoms joined by OR produce an OrCondition."""
        node = parse_condition("goal:S4:completed OR event:employed")
        assert isinstance(node, OrCondition)
        assert len(node.children) == 2

    def test_and_binds_tighter_than_or(self):
        """A OR B AND C  should parse as  A OR (B AND C)."""
        # i.e. the OrCondition has children [A, AndCondition([B, C])]
        node = parse_condition("event:a OR goal:B:active AND goal:C:active")
        assert isinstance(node, OrCondition)
        assert len(node.children) == 2
        # first child is the event atom
        assert isinstance(node.children[0], EventCondition)
        # second child is the AND group
        assert isinstance(node.children[1], AndCondition)

    def test_parse_parens_override_precedence(self):
        """(A OR B) AND C  → AndCondition(OrCondition(A, B), C)."""
        node = parse_condition("(event:a OR event:b) AND goal:C:active")
        assert isinstance(node, AndCondition)
        assert isinstance(node.children[0], OrCondition)

    def test_parse_three_and(self):
        """Three atoms with AND produces a single AndCondition with 3 children."""
        node = parse_condition(
            "goal:S4:completed AND goal:S5:completed AND event:employed"
        )
        assert isinstance(node, AndCondition)
        assert len(node.children) == 3

    # ── Whitespace handling ─────────────────────────────────────────────────

    def test_extra_whitespace_accepted(self):
        """Leading/trailing/extra internal whitespace is tolerated."""
        node = parse_condition("  goal:S4:completed   AND   event:employed  ")
        assert isinstance(node, AndCondition)

    # ── Error cases ────────────────────────────────────────────────────────

    def test_empty_string_raises(self):
        """Empty condition string must raise ConditionParseError."""
        with pytest.raises(ConditionParseError):
            parse_condition("")

    def test_whitespace_only_raises(self):
        """Whitespace-only string has no tokens — raises ConditionParseError."""
        with pytest.raises(ConditionParseError):
            parse_condition("   ")

    def test_invalid_token_raises(self):
        """Stray characters not in the grammar raise ConditionParseError."""
        with pytest.raises(ConditionParseError):
            parse_condition("goal:S4:completed; DROP TABLE goals")

    def test_sql_injection_attempt_raises(self):
        """SQL injection characters are not in the token grammar — rejected cleanly."""
        with pytest.raises(ConditionParseError):
            parse_condition("goal:S4:completed' OR '1'='1")

    def test_script_tag_raises(self):
        """HTML/script tags are not valid DSL — rejected by tokenizer."""
        with pytest.raises(ConditionParseError):
            parse_condition("<script>alert(1)</script>")

    def test_bare_and_raises(self):
        """AND without operands on both sides must fail."""
        with pytest.raises(ConditionParseError):
            parse_condition("AND")

    def test_unclosed_paren_raises(self):
        """Unclosed parenthesis should raise ConditionParseError."""
        with pytest.raises(ConditionParseError):
            parse_condition("(goal:S4:completed AND event:employed")

    def test_extra_closing_paren_raises(self):
        """Extra closing parenthesis raises ConditionParseError."""
        with pytest.raises(ConditionParseError):
            parse_condition("goal:S4:completed)")

    def test_nesting_depth_limit(self):
        """More than 10 levels of parenthesis nesting must raise ConditionParseError."""
        deep = "(" * 11 + "goal:S4:completed" + ")" * 11
        with pytest.raises(ConditionParseError, match="Nesting depth"):
            parse_condition(deep)

    def test_just_at_depth_limit_is_ok(self):
        """Exactly 10 levels of nesting is allowed (limit is exclusive at 10)."""
        # 10 levels is fine; 11 triggers the guard
        at_limit = "(" * 10 + "goal:S4:completed" + ")" * 10
        node = parse_condition(at_limit)
        assert isinstance(node, GoalCondition)


# ═══════════════════════════════════════════════════════════════════════════
# 2. ACTIVATION ENGINE — Validator
# ═══════════════════════════════════════════════════════════════════════════


class TestValidator:
    """Tests for validate_condition() — the write-time wrapper around parse_condition."""

    def test_none_returns_none(self):
        assert validate_condition(None) is None

    def test_empty_string_returns_none(self):
        assert validate_condition("") is None

    def test_blank_string_returns_none(self):
        assert validate_condition("   ") is None

    def test_valid_string_returns_ast(self):
        node = validate_condition("goal:S4:completed")
        assert isinstance(node, GoalCondition)

    def test_invalid_string_raises(self):
        with pytest.raises(ConditionParseError):
            validate_condition("NOT_VALID!!!")


# ═══════════════════════════════════════════════════════════════════════════
# 3. ACTIVATION ENGINE — Evaluator
# ═══════════════════════════════════════════════════════════════════════════


class TestEvaluator:
    """Tests for evaluate_condition() — AST evaluation against live DB state."""

    # ── GoalCondition leaf ─────────────────────────────────────────────────

    def test_goal_condition_true_when_status_matches(self, session):
        """GoalCondition is True when the goal exists and activation_status matches."""
        _make_goal(session, name="Target", pyramid_id="S4", activation_status="COMPLETED")
        node = GoalCondition(pyramid_id="S4", status="completed")
        assert evaluate_condition(node, session, "tester") is True

    def test_goal_condition_false_when_status_mismatch(self, session):
        """GoalCondition is False when the status doesn't match, even if the goal exists."""
        _make_goal(session, name="Target", pyramid_id="S4", activation_status="ACTIVE")
        node = GoalCondition(pyramid_id="S4", status="completed")
        assert evaluate_condition(node, session, "tester") is False

    def test_goal_condition_false_when_goal_missing(self, session):
        """GoalCondition is False (not an error) when the pyramid_id doesn't exist."""
        node = GoalCondition(pyramid_id="NONEXISTENT", status="completed")
        assert evaluate_condition(node, session, "tester") is False

    def test_goal_condition_case_insensitive_status(self, session):
        """Status comparison is case-insensitive on both sides."""
        _make_goal(session, name="Target", pyramid_id="V1", activation_status="ACTIVE")
        # AST stores status lowercased, DB stores it uppercase — should match
        node = GoalCondition(pyramid_id="V1", status="active")
        assert evaluate_condition(node, session, "tester") is True

    # ── EventCondition leaf ────────────────────────────────────────────────

    def test_event_condition_true_when_occurred(self, session):
        """EventCondition is True when the LifeEvent exists and occurred=True."""
        _make_event(session, event_key="employed", occurred=True)
        node = EventCondition(event_key="employed")
        assert evaluate_condition(node, session, "tester") is True

    def test_event_condition_false_when_not_occurred(self, session):
        """EventCondition is False when the event exists but occurred=False."""
        _make_event(session, event_key="employed", occurred=False)
        node = EventCondition(event_key="employed")
        assert evaluate_condition(node, session, "tester") is False

    def test_event_condition_false_when_missing(self, session):
        """EventCondition is False (not an error) when the event key doesn't exist."""
        node = EventCondition(event_key="married")
        assert evaluate_condition(node, session, "tester") is False

    # ── AndCondition ───────────────────────────────────────────────────────

    def test_and_true_when_all_children_true(self, session):
        """AndCondition is True only when every child is True."""
        _make_goal(session, name="G1", pyramid_id="S4", activation_status="COMPLETED")
        _make_event(session, event_key="employed", occurred=True)
        node = AndCondition(children=(
            GoalCondition(pyramid_id="S4", status="completed"),
            EventCondition(event_key="employed"),
        ))
        assert evaluate_condition(node, session, "tester") is True

    def test_and_false_when_any_child_false(self, session):
        """AndCondition is False if even one child is False."""
        _make_goal(session, name="G1", pyramid_id="S4", activation_status="COMPLETED")
        # 'employed' event is not created — evaluates to False
        node = AndCondition(children=(
            GoalCondition(pyramid_id="S4", status="completed"),
            EventCondition(event_key="employed"),
        ))
        assert evaluate_condition(node, session, "tester") is False

    # ── OrCondition ────────────────────────────────────────────────────────

    def test_or_true_when_any_child_true(self, session):
        """OrCondition is True when at least one child is True."""
        # 'S6' exists but is still PENDING — False
        _make_goal(session, name="G2", pyramid_id="S6", activation_status="PENDING")
        # 'employed' event is True
        _make_event(session, event_key="employed", occurred=True)
        node = OrCondition(children=(
            GoalCondition(pyramid_id="S6", status="completed"),
            EventCondition(event_key="employed"),
        ))
        assert evaluate_condition(node, session, "tester") is True

    def test_or_false_when_all_children_false(self, session):
        """OrCondition is False when no child is True."""
        node = OrCondition(children=(
            GoalCondition(pyramid_id="MISSING_A", status="completed"),
            GoalCondition(pyramid_id="MISSING_B", status="completed"),
        ))
        assert evaluate_condition(node, session, "tester") is False

    # ── Mixed AND/OR ───────────────────────────────────────────────────────

    def test_complex_and_or_condition(self, session):
        """goal:S4:completed AND (goal:S5:completed OR event:employed) — True when S4+event."""
        _make_goal(session, name="G1", pyramid_id="S4", activation_status="COMPLETED")
        _make_goal(session, name="G2", pyramid_id="S5", activation_status="ACTIVE")
        _make_event(session, event_key="employed", occurred=True)
        # S4:completed=True AND (S5:completed=False OR employed=True) → True AND True → True
        node = AndCondition(children=(
            GoalCondition(pyramid_id="S4", status="completed"),
            OrCondition(children=(
                GoalCondition(pyramid_id="S5", status="completed"),
                EventCondition(event_key="employed"),
            )),
        ))
        assert evaluate_condition(node, session, "tester") is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. ACTIVATION ENGINE — Auto-activation hook
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckAndUpdateActivations:
    """Tests for check_and_update_activations() — the cascade hook."""

    def test_pending_goal_activated_when_condition_met(self, session):
        """A PENDING goal whose condition is now True should flip to ACTIVE."""
        _make_goal(
            session, name="Prereq", pyramid_id="S4", activation_status="COMPLETED"
        )
        # Target is PENDING, condition requires S4 to be completed
        target = _make_goal(
            session,
            name="Target",
            pyramid_id="T4",
            activation_status="PENDING",
            activation_condition="goal:S4:completed",
        )
        session.commit()

        activated = check_and_update_activations(session, "tester")
        session.commit()

        session.refresh(target)
        assert target.activation_status == "ACTIVE"
        assert any(g.id == target.id for g in activated)

    def test_pending_goal_not_activated_when_condition_not_met(self, session):
        """A PENDING goal stays PENDING if its condition is still False."""
        # Prereq S4 is ACTIVE, not COMPLETED — condition expects 'completed'
        _make_goal(session, name="Prereq", pyramid_id="S4", activation_status="ACTIVE")
        target = _make_goal(
            session,
            name="Target",
            pyramid_id="T4",
            activation_status="PENDING",
            activation_condition="goal:S4:completed",
        )
        session.commit()

        activated = check_and_update_activations(session, "tester")

        session.refresh(target)
        assert target.activation_status == "PENDING"
        assert activated == []

    def test_already_active_goals_are_not_touched(self, session):
        """ACTIVE and COMPLETED goals are never re-processed by the hook."""
        active = _make_goal(session, name="Active", pyramid_id="A1", activation_status="ACTIVE")
        completed = _make_goal(session, name="Done", pyramid_id="C1", activation_status="COMPLETED")
        session.commit()

        activated = check_and_update_activations(session, "tester")

        session.refresh(active)
        session.refresh(completed)
        assert active.activation_status == "ACTIVE"     # unchanged
        assert completed.activation_status == "COMPLETED"  # unchanged
        assert activated == []

    def test_cascade_across_two_passes(self, session):
        """Goals activated in pass 1 can unlock goals in pass 2 (cascade)."""
        # G1 is COMPLETED — triggers G2 to activate in pass 1.
        _make_goal(session, name="G1", pyramid_id="P1", activation_status="COMPLETED")
        # G2 is PENDING, condition: G1 completed — activates in pass 1.
        g2 = _make_goal(
            session, name="G2", pyramid_id="P2",
            activation_status="PENDING", activation_condition="goal:P1:completed",
        )
        # G3 is PENDING, condition: G2 active — activates in pass 2 (after G2 flips).
        g3 = _make_goal(
            session, name="G3", pyramid_id="P3",
            activation_status="PENDING", activation_condition="goal:P2:active",
        )
        session.commit()

        activated = check_and_update_activations(session, "tester")
        session.commit()

        session.refresh(g2)
        session.refresh(g3)
        activated_ids = {g.id for g in activated}

        assert g2.activation_status == "ACTIVE"
        assert g3.activation_status == "ACTIVE"
        assert g2.id in activated_ids
        assert g3.id in activated_ids

    def test_event_triggers_activation(self, session):
        """A LifeEvent occurring is enough to satisfy an event: condition."""
        _make_event(session, event_key="child_born", occurred=True)
        target = _make_goal(
            session, name="Child Fund", pyramid_id="T11",
            activation_status="PENDING", activation_condition="event:child_born",
        )
        session.commit()

        check_and_update_activations(session, "tester")
        session.commit()

        session.refresh(target)
        assert target.activation_status == "ACTIVE"


# ═══════════════════════════════════════════════════════════════════════════
# 5. GOAL GRAPH — validate_link (cycle + self-link detection)
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateLink:
    """Tests for validate_link() — self-link guard and cycle detection."""

    def test_self_link_raises_400(self, session):
        """A goal cannot link to itself."""
        g = _make_goal(session, name="Self", pyramid_id="V1")
        session.commit()
        with pytest.raises(HTTPException) as exc_info:
            validate_link(session, g.id, g.id, "tester")
        assert exc_info.value.status_code == 400

    def test_valid_link_does_not_raise(self, session):
        """A valid new edge (no cycle) should complete without exception."""
        parent = _make_goal(session, name="Vision", pyramid_id="V1", tier="VISION")
        child = _make_goal(session, name="Strategy", pyramid_id="S1", tier="STRATEGY")
        session.commit()
        validate_link(session, parent.id, child.id, "tester")  # must not raise

    def test_direct_cycle_raises_400(self, session):
        """Adding B→A when A→B already exists must raise 400."""
        a = _make_goal(session, name="A", pyramid_id="A1")
        b = _make_goal(session, name="B", pyramid_id="B1")
        _make_link(session, parent=a, child=b)
        session.commit()

        # Now try to add B → A which would close the cycle.
        with pytest.raises(HTTPException) as exc_info:
            validate_link(session, b.id, a.id, "tester")
        assert exc_info.value.status_code == 400
        assert "cycle" in exc_info.value.detail.lower()

    def test_transitive_cycle_raises_400(self, session):
        """A → B → C; adding C → A must be rejected as a transitive cycle."""
        a = _make_goal(session, name="A", pyramid_id="A1")
        b = _make_goal(session, name="B", pyramid_id="B1")
        c = _make_goal(session, name="C", pyramid_id="C1")
        _make_link(session, parent=a, child=b)
        _make_link(session, parent=b, child=c)
        session.commit()

        with pytest.raises(HTTPException) as exc_info:
            validate_link(session, c.id, a.id, "tester")
        assert exc_info.value.status_code == 400

    def test_diamond_dag_is_accepted(self, session):
        """Diamond shape (A→B, A→C, B→D, C→D) is a valid DAG — no cycle."""
        a = _make_goal(session, name="A", pyramid_id="A1")
        b = _make_goal(session, name="B", pyramid_id="B1")
        c = _make_goal(session, name="C", pyramid_id="C1")
        d = _make_goal(session, name="D", pyramid_id="D1")
        _make_link(session, parent=a, child=b)
        _make_link(session, parent=a, child=c)
        _make_link(session, parent=b, child=d)
        session.commit()

        # Adding C→D completes the diamond but does NOT create a cycle.
        validate_link(session, c.id, d.id, "tester")  # must not raise

    def test_missing_parent_raises_404(self, session):
        """validate_link raises 404 when the parent goal doesn't exist."""
        child = _make_goal(session, name="Child", pyramid_id="C1")
        session.commit()
        with pytest.raises(HTTPException) as exc_info:
            validate_link(session, 99999, child.id, "tester")
        assert exc_info.value.status_code == 404

    def test_cross_user_raises_400(self, session):
        """Goals owned by different users cannot be linked."""
        parent = _make_goal(session, name="Parent", pyramid_id="P1", user_id="alice")
        child = _make_goal(session, name="Child", pyramid_id="C1", user_id="bob")
        session.commit()
        with pytest.raises(HTTPException) as exc_info:
            validate_link(session, parent.id, child.id, "alice")
        assert exc_info.value.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# 6. GOAL GRAPH — get_goal_tree
# ═══════════════════════════════════════════════════════════════════════════


class TestGetGoalTree:
    """Tests for get_goal_tree() — tier-grouped hierarchy response."""

    def test_empty_user_returns_empty_buckets(self, session):
        """No goals → all tier buckets are empty lists, links is empty."""
        tree = get_goal_tree(session, "nobody")
        assert tree["vision"] == []
        assert tree["strategy"] == []
        assert tree["tactic"] == []
        assert tree["operational"] == []
        assert tree["untiered"] == []
        assert tree["links"] == []

    def test_goals_bucketed_by_tier(self, session):
        """Goals are placed into the correct tier bucket."""
        _make_goal(session, name="V1", tier="VISION", pyramid_id="V1")
        _make_goal(session, name="S1", tier="STRATEGY", pyramid_id="S1")
        _make_goal(session, name="T1", tier="TACTIC", pyramid_id="T1")
        _make_goal(session, name="O1", tier="OPERATIONAL", pyramid_id="O1")
        _make_goal(session, name="NoBucket")  # no tier → untiered
        session.commit()

        tree = get_goal_tree(session, "tester")
        assert len(tree["vision"]) == 1
        assert len(tree["strategy"]) == 1
        assert len(tree["tactic"]) == 1
        assert len(tree["operational"]) == 1
        assert len(tree["untiered"]) == 1

    def test_links_included_in_tree(self, session):
        """GoalLinks for the user appear in the 'links' list."""
        v = _make_goal(session, name="Vision", tier="VISION", pyramid_id="V1")
        s = _make_goal(session, name="Strategy", tier="STRATEGY", pyramid_id="S1")
        _make_link(session, parent=v, child=s)
        session.commit()

        tree = get_goal_tree(session, "tester")
        assert len(tree["links"]) == 1
        assert tree["links"][0]["parent_goal_id"] == v.id
        assert tree["links"][0]["child_goal_id"] == s.id

    def test_no_cross_user_data_leakage(self, session):
        """Goals from another user must not appear in the tree."""
        _make_goal(session, name="Other user goal", user_id="other_user")
        _make_goal(session, name="My goal", user_id="tester", tier="VISION")
        session.commit()

        tree = get_goal_tree(session, "tester")
        all_goals = (
            tree["vision"] + tree["strategy"] + tree["tactic"] +
            tree["operational"] + tree["untiered"]
        )
        assert all(g["user_id"] == "tester" for g in all_goals)

    def test_bucket_sorting_by_allocation_priority(self, session):
        """Goals within a bucket are sorted by allocation_priority (lowest = first)."""
        _make_goal(session, name="Prio3", tier="STRATEGY", allocation_priority=3)
        _make_goal(session, name="Prio1", tier="STRATEGY", allocation_priority=1)
        _make_goal(session, name="PrioNull", tier="STRATEGY", allocation_priority=None)
        session.commit()

        tree = get_goal_tree(session, "tester")
        strategy = tree["strategy"]
        # Priority 1 first, then 3, then null
        assert strategy[0]["name"] == "Prio1"
        assert strategy[1]["name"] == "Prio3"
        assert strategy[2]["name"] == "PrioNull"


# ═══════════════════════════════════════════════════════════════════════════
# 7. GOAL GRAPH — get_ancestors & get_descendants
# ═══════════════════════════════════════════════════════════════════════════


class TestAncestorsDescendants:
    """Tests for get_ancestors() and get_descendants()."""

    def _build_chain(self, session) -> tuple[Goal, Goal, Goal, Goal]:
        """Build a simple linear chain: vision → strategy → tactic → operational."""
        v = _make_goal(session, name="Vision", tier="VISION", pyramid_id="V1")
        s = _make_goal(session, name="Strategy", tier="STRATEGY", pyramid_id="S1")
        t = _make_goal(session, name="Tactic", tier="TACTIC", pyramid_id="T1")
        o = _make_goal(session, name="Ops", tier="OPERATIONAL", pyramid_id="O1")
        _make_link(session, parent=v, child=s)
        _make_link(session, parent=s, child=t)
        _make_link(session, parent=t, child=o)
        session.commit()
        return v, s, t, o

    # ── Ancestors ──────────────────────────────────────────────────────────

    def test_ancestors_of_leaf(self, session):
        """get_ancestors of an operational leaf returns tactic, strategy, vision (BFS order)."""
        v, s, t, o = self._build_chain(session)
        ancestors = get_ancestors(session, o.id, "tester")
        ancestor_ids = [g.id for g in ancestors]
        # Immediate parent (tactic) should come before grandparent (strategy) etc.
        assert ancestor_ids[0] == t.id
        assert s.id in ancestor_ids
        assert v.id in ancestor_ids
        assert o.id not in ancestor_ids  # source never in result

    def test_ancestors_of_root_is_empty(self, session):
        """A root goal (Vision) with no parents returns an empty ancestor list."""
        v, _, _, _ = self._build_chain(session)
        assert get_ancestors(session, v.id, "tester") == []

    def test_ancestors_raises_404_for_unknown_goal(self, session):
        """Requesting ancestors for a non-existent goal_id raises 404."""
        with pytest.raises(HTTPException) as exc_info:
            get_ancestors(session, 99999, "tester")
        assert exc_info.value.status_code == 404

    # ── Descendants ────────────────────────────────────────────────────────

    def test_descendants_of_root(self, session):
        """get_descendants of Vision returns strategy, tactic, operational (BFS)."""
        v, s, t, o = self._build_chain(session)
        desc = get_descendants(session, v.id, "tester")
        desc_ids = [g.id for g in desc]
        assert desc_ids[0] == s.id  # immediate child first
        assert t.id in desc_ids
        assert o.id in desc_ids
        assert v.id not in desc_ids  # source never in result

    def test_descendants_of_leaf_is_empty(self, session):
        """A leaf node (Operational) has no descendants."""
        _, _, _, o = self._build_chain(session)
        assert get_descendants(session, o.id, "tester") == []

    def test_descendants_raises_404_for_unknown_goal(self, session):
        with pytest.raises(HTTPException) as exc_info:
            get_descendants(session, 99999, "tester")
        assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 8. GOAL GRAPH — get_impact
# ═══════════════════════════════════════════════════════════════════════════


class TestGetImpact:
    """Tests for get_impact() — bidirectional connectivity with distance + link_type."""

    def test_impact_returns_both_directions(self, session):
        """For a middle node in a chain, impact must include both ancestors and descendants."""
        v = _make_goal(session, name="Vision", pyramid_id="V1")
        s = _make_goal(session, name="Strategy", pyramid_id="S1")
        o = _make_goal(session, name="Ops", pyramid_id="O1")
        _make_link(session, parent=v, child=s)
        _make_link(session, parent=s, child=o)
        session.commit()

        impact = get_impact(session, s.id, "tester")
        directions = {r["direction"] for r in impact}
        assert "ancestor" in directions
        assert "descendant" in directions

    def test_impact_distance_one_for_direct_neighbors(self, session):
        """Direct neighbors have distance=1."""
        v = _make_goal(session, name="Vision", pyramid_id="V1")
        s = _make_goal(session, name="Strategy", pyramid_id="S1")
        _make_link(session, parent=v, child=s)
        session.commit()

        impact = get_impact(session, s.id, "tester")
        assert len(impact) == 1
        assert impact[0]["distance"] == 1
        assert impact[0]["direction"] == "ancestor"

    def test_impact_sorted_by_distance(self, session):
        """Impact results are sorted nearest first (distance ascending)."""
        v = _make_goal(session, name="V", pyramid_id="V1")
        s = _make_goal(session, name="S", pyramid_id="S1")
        o = _make_goal(session, name="O", pyramid_id="O1")
        _make_link(session, parent=v, child=s)
        _make_link(session, parent=s, child=o)
        session.commit()

        impact = get_impact(session, s.id, "tester")
        distances = [r["distance"] for r in impact]
        assert distances == sorted(distances)

    def test_impact_raises_404_for_unknown_goal(self, session):
        with pytest.raises(HTTPException) as exc_info:
            get_impact(session, 99999, "tester")
        assert exc_info.value.status_code == 404

    def test_source_not_in_impact_result(self, session):
        """The queried goal itself must never appear in the impact list."""
        v = _make_goal(session, name="V", pyramid_id="V1")
        s = _make_goal(session, name="S", pyramid_id="S1")
        _make_link(session, parent=v, child=s)
        session.commit()

        impact = get_impact(session, s.id, "tester")
        result_ids = [r["goal"]["id"] for r in impact]
        assert s.id not in result_ids


# ═══════════════════════════════════════════════════════════════════════════
# 9. GOAL GRAPH — get_allocation_summary
# ═══════════════════════════════════════════════════════════════════════════


class TestGetAllocationSummary:
    """Tests for get_allocation_summary() — monthly INR aggregation."""

    def test_empty_returns_zero(self, session):
        """No goals → total_allocated is 0.0 and goals list is empty."""
        result = get_allocation_summary(session, "tester")
        assert result["total_allocated"] == 0.0
        assert result["goals"] == []

    def test_only_active_goals_counted(self, session):
        """PENDING and COMPLETED goals are excluded even if they have allocations."""
        _make_goal(session, name="Active", activation_status="ACTIVE", monthly_allocation=10000)
        _make_goal(session, name="Pending", activation_status="PENDING", monthly_allocation=5000)
        _make_goal(session, name="Done", activation_status="COMPLETED", monthly_allocation=2000)
        session.commit()

        result = get_allocation_summary(session, "tester")
        assert result["total_allocated"] == 10000.0
        assert len(result["goals"]) == 1
        assert result["goals"][0]["name"] == "Active"

    def test_goals_without_allocation_excluded(self, session):
        """ACTIVE goals with monthly_allocation=None are excluded from the summary."""
        _make_goal(session, name="NoAlloc", activation_status="ACTIVE", monthly_allocation=None)
        _make_goal(session, name="HasAlloc", activation_status="ACTIVE", monthly_allocation=5000)
        session.commit()

        result = get_allocation_summary(session, "tester")
        assert result["total_allocated"] == 5000.0
        assert len(result["goals"]) == 1

    def test_total_is_sum_of_all_active(self, session):
        """total_allocated is the rounded sum of all ACTIVE monthly allocations."""
        _make_goal(session, name="G1", activation_status="ACTIVE", monthly_allocation=15000)
        _make_goal(session, name="G2", activation_status="ACTIVE", monthly_allocation=25000.5)
        session.commit()

        result = get_allocation_summary(session, "tester")
        assert result["total_allocated"] == round(15000 + 25000.5, 2)

    def test_goals_sorted_by_allocation_priority(self, session):
        """Goals in the summary are ordered by allocation_priority ascending (nulls last)."""
        _make_goal(session, name="Prio3", activation_status="ACTIVE",
                   monthly_allocation=1000, allocation_priority=3)
        _make_goal(session, name="Prio1", activation_status="ACTIVE",
                   monthly_allocation=2000, allocation_priority=1)
        _make_goal(session, name="PrioNull", activation_status="ACTIVE",
                   monthly_allocation=500, allocation_priority=None)
        session.commit()

        result = get_allocation_summary(session, "tester")
        names = [g["name"] for g in result["goals"]]
        assert names.index("Prio1") < names.index("Prio3")
        assert names.index("Prio3") < names.index("PrioNull")

    def test_no_cross_user_leakage(self, session):
        """Allocation summary never returns data from another user."""
        _make_goal(session, name="Other", activation_status="ACTIVE",
                   monthly_allocation=99999, user_id="attacker")
        _make_goal(session, name="Mine", activation_status="ACTIVE",
                   monthly_allocation=1000, user_id="tester")
        session.commit()

        result = get_allocation_summary(session, "tester")
        assert result["total_allocated"] == 1000.0
