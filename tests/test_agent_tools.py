"""
Smoke tests for agent tool registration and pure formatters (no LLM calls).
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import agent_internal_token


def test_tool_registry_includes_planned_tools() -> None:
    from agent.tools import get_all_tools

    names = {t.name for t in get_all_tools()}
    # 3 foundation + 13 other tools (top expenses merged into get_spending_by_category) = 17
    assert len(names) == 17
    for required in (
        "get_spending_summary",
        "get_spending_by_category",
        "get_spending_trends",
        "get_recurring_expenses",
        "search_transactions",
        "get_net_worth",
        "get_holdings_breakdown",
        "get_net_worth_trend",
        "get_investment_activity",
        "get_goals_overview",
        "get_goal_detail",
        "get_surplus_allocation",
        "run_projection",
        "compare_scenarios",
        "simulate_surplus_change",
        "get_user_profile",
        "get_date_context",
    ):
        assert required in names, f"missing tool: {required}"


def test_format_spending_by_category_derived_fields() -> None:
    from agent.tools.spending import format_spending_by_category_for_agent

    rows = [
        {"category": "Food", "amount": 100.0, "percentage": 60.0, "txn_count": 3},
        {"category": "Travel", "amount": 40.0, "percentage": 24.0, "txn_count": 1},
    ]
    out = format_spending_by_category_for_agent(rows)
    assert out["status"] == "success"
    assert out["category_count"] == 2
    assert out["categories"][0]["rank"] == 1
    assert out["top_3_categories_share_pct"] == pytest.approx(84.0)

    large = [{"txn_date": "2026-04-01", "amount": 9000.0, "counterparty": "Uber"}]
    out2 = format_spending_by_category_for_agent(rows, large_transactions=large)
    assert "large_transactions" in out2
    assert out2["large_transactions"][0]["counterparty"] == "Uber"


def test_get_date_context_asyncio_wrapper() -> None:
    """Run async test without requiring ``pytest-asyncio`` in all environments."""
    asyncio.run(_run_date_context_once())


async def _run_date_context_once() -> None:
    from agent.tools import get_tool

    from api.main import app

    tok = agent_internal_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Arth-Internal": tok},
        timeout=60.0,
    ) as client:
        tool = get_tool("get_date_context")
        assert tool is not None
        out = await tool.execute(client, "{}")
    assert out.get("status") == "success"
    assert str(out.get("fiscal_year_label", "")).startswith("FY ")


def test_resolve_goal_picks_numeric_id() -> None:
    """Resolver logic without HTTP: unit-test scoring on synthetic rows."""
    from agent.tools._resolvers import resolve_goal

    class _FakeResp:
        def __init__(self, data: list[dict]) -> None:
            self._data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return self._data

    class _FakeClient:
        def __init__(self, goals: list[dict]) -> None:
            self._goals = goals

        async def get(self, path: str, params: dict | None = None) -> _FakeResp:
            assert path == "/api/goals"
            return _FakeResp(self._goals)

    goals = [
        {"id": 1, "name": "House down payment", "system_priority_score": 10.0},
        {"id": 2, "name": "Emergency fund", "system_priority_score": 20.0},
    ]

    async def run() -> None:
        c = _FakeClient(goals)
        g = await resolve_goal(c, "2")  # type: ignore[arg-type]
        assert g is not None
        assert g["id"] == 2
        g2 = await resolve_goal(c, "emergency")  # type: ignore[arg-type]
        assert g2 is not None
        assert "Emergency" in g2["name"]

    asyncio.run(run())
