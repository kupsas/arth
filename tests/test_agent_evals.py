"""Unit tests for agent eval harness (no live LLM)."""

from __future__ import annotations

from pathlib import Path

from agent.evals.dataset import EvalQuestion, load_eval_questions
from agent.evals.report import compare_runs, generate_report
from agent.evals.scorer import auto_score_question_result


def test_load_all_33_questions() -> None:
    qs = load_eval_questions()
    assert len(qs) == 33
    ids = {q.id for q in qs}
    assert "t1_q01" in ids and "t4_q33" in ids


def test_filter_tier() -> None:
    t1 = load_eval_questions(tier=1)
    assert len(t1) == 10
    assert all(q.tier == 1 for q in t1)


def test_filter_ids() -> None:
    qs = load_eval_questions(question_ids=frozenset({"t1_q01", "t4_q27"}))
    assert len(qs) == 2


def test_auto_score_screening_block() -> None:
    q = EvalQuestion(
        id="x",
        tier=4,
        question="off",
        expected_tools=(),
        tool_match_mode="skip",
        expected_screening="block",
        expected_screening_categories=("off_topic",),
        boundary_type="off_topic",
        expected_behavior="",
        scoring_notes=None,
    )
    r = auto_score_question_result(
        q,
        screening_enabled=True,
        screening_allowed=False,
        screening_category="off_topic",
        tools_called=[],
        response_text="Sorry, not my domain.",
        agent_ran=False,
        error=None,
    )
    assert r["overall_pass"] is True


def test_auto_score_tool_contains_all() -> None:
    q = EvalQuestion(
        id="x",
        tier=1,
        question="q",
        expected_tools=("get_net_worth",),
        tool_match_mode="contains_all",
        expected_screening="allow",
        expected_screening_categories=None,
        boundary_type=None,
        expected_behavior="",
        scoring_notes=None,
    )
    tools = [{"name": "get_date_context", "arguments": {}, "result": {}, "duration_ms": 1}]
    r = auto_score_question_result(
        q,
        screening_enabled=True,
        screening_allowed=True,
        screening_category=None,
        tools_called=tools,
        response_text="",
        agent_ran=True,
        error=None,
    )
    # Missing get_net_worth
    assert r["overall_pass"] is False

    tools2 = [
        {"name": "get_net_worth", "arguments": {}, "result": {}, "duration_ms": 1},
        {"name": "get_date_context", "arguments": {}, "result": {}, "duration_ms": 1},
    ]
    r2 = auto_score_question_result(
        q,
        screening_enabled=True,
        screening_allowed=True,
        screening_category=None,
        tools_called=tools2,
        response_text="hello",
        agent_ran=True,
        error=None,
    )
    assert r2["overall_pass"] is True


def test_auto_score_tool_equivalence_net_worth_holdings() -> None:
    """get_net_worth and get_holdings_breakdown satisfy each other's ``contains_all`` expectation."""
    q = EvalQuestion(
        id="x",
        tier=1,
        question="allocation",
        expected_tools=("get_holdings_breakdown",),
        tool_match_mode="contains_all",
        expected_screening="allow",
        expected_screening_categories=None,
        boundary_type=None,
        expected_behavior="",
        scoring_notes=None,
    )
    tools = [{"name": "get_net_worth", "arguments": {}, "result": {}, "duration_ms": 1}]
    r = auto_score_question_result(
        q,
        screening_enabled=True,
        screening_allowed=True,
        screening_category=None,
        tools_called=tools,
        response_text="ok",
        agent_ran=True,
        error=None,
    )
    assert r["overall_pass"] is True

    q2 = EvalQuestion(
        id="x",
        tier=2,
        question="liquid",
        expected_tools=("get_net_worth", "get_holdings_breakdown"),
        tool_match_mode="contains_all",
        expected_screening="allow",
        expected_screening_categories=None,
        boundary_type=None,
        expected_behavior="",
        scoring_notes=None,
    )
    tools_one = [{"name": "get_net_worth", "arguments": {}, "result": {}, "duration_ms": 1}]
    r2 = auto_score_question_result(
        q2,
        screening_enabled=True,
        screening_allowed=True,
        screening_category=None,
        tools_called=tools_one,
        response_text="ok",
        agent_ran=True,
        error=None,
    )
    assert r2["overall_pass"] is True


def test_auto_score_tool_equivalence_spend_period() -> None:
    q = EvalQuestion(
        id="x",
        tier=4,
        question="old spend",
        expected_tools=("search_transactions",),
        tool_match_mode="contains_all",
        expected_screening="allow",
        expected_screening_categories=None,
        boundary_type=None,
        expected_behavior="",
        scoring_notes=None,
    )
    tools = [{"name": "get_spending_summary", "arguments": {}, "result": {}, "duration_ms": 1}]
    r = auto_score_question_result(
        q,
        screening_enabled=True,
        screening_allowed=True,
        screening_category=None,
        tools_called=tools,
        response_text="ok",
        agent_ran=True,
        error=None,
    )
    assert r["overall_pass"] is True


def test_pii_scan_fails_on_pan_like() -> None:
    q = EvalQuestion(
        id="x",
        tier=1,
        question="q",
        expected_tools=(),
        tool_match_mode="skip",
        expected_screening="allow",
        expected_screening_categories=None,
        boundary_type=None,
        expected_behavior="",
        scoring_notes=None,
    )
    r = auto_score_question_result(
        q,
        screening_enabled=True,
        screening_allowed=True,
        screening_category=None,
        tools_called=[],
        response_text="Your PAN is ABCDE1234F for reference.",
        agent_ran=True,
        error=None,
    )
    assert r["overall_pass"] is False


def test_compare_runs_empty(tmp_path: Path) -> None:
    p = compare_runs(tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "result files" in text.lower()


def test_generate_report_minimal(tmp_path: Path) -> None:
    js = tmp_path / "mini.json"
    js.write_text(
        """
{
  "run_id": "test",
  "started_utc": "2026-01-01T00:00:00Z",
  "agent_model": "test/model",
  "screening_enabled": true,
  "questions": [
    {
      "id": "t1_q01",
      "tier": 1,
      "question": "Hello?",
      "expected_behavior": "",
      "scoring_notes": null,
      "boundary_type": null,
      "expected_tools": [],
      "tool_match_mode": "skip",
      "screening": {"allowed": true, "category": null, "layer": "llm_classifier", "latency_ms": 1},
      "duration_s": 1.0,
      "cost_usd_delta": 0.0,
      "tools_called": [],
      "llm_steps": [],
      "response": "Hi",
      "error": null,
      "manual_scores": {"parameter_accuracy": null, "synthesis_quality": 4, "boundary_awareness": null, "notes": null},
      "auto_scores": {"overall_pass": true, "checks": []}
    }
  ],
  "totals": {"question_count": 1, "auto_pass_count": 1, "auto_fail_count": 0, "wall_duration_s": 1.0, "total_cost_usd": 0.0}
}
""".strip(),
        encoding="utf-8",
    )
    out = generate_report(js)
    assert out.exists()
    assert "t1_q01" in out.read_text(encoding="utf-8")
