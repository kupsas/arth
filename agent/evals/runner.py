"""
Async eval runner — full stack (screening + ReAct) like the CLI, one question per memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent import config as cfg
from agent.client import create_agent_http_client
from agent.core import run_agent_turn
from agent.evals.dataset import EvalQuestion
from agent.evals.scorer import auto_score_question_result
from agent.events import (
    AgentEvent,
    LlmStepEvent,
    ResponseEvent,
    ToolCallCompleted,
    ToolCallStarted,
)
from agent.memory import ConversationMemory
from agent.profile import generate_user_profile
from agent.security import CostTracker, screen_message

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _model_slug_for_filename(model: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", model.strip())
    return s[:120] if len(s) > 120 else s


@contextmanager
def _agent_model_override(model: str | None) -> Any:
    """Force a single LiteLLM model id for benchmarking (no fallback chain)."""
    if not model:
        yield
        return
    prev_m = cfg.AGENT_MODEL
    prev_fb = list(cfg.AGENT_FALLBACK_CHAIN)
    try:
        # Mutate module globals — llm.chat_completion reads these each call.
        cfg.AGENT_MODEL = model.strip()
        cfg.AGENT_FALLBACK_CHAIN = [cfg.AGENT_MODEL]
        yield
    finally:
        cfg.AGENT_MODEL = prev_m
        cfg.AGENT_FALLBACK_CHAIN = prev_fb


@dataclass
class EvalEventCollector:
    """
    Implements the CLI-style ``event_callback`` for :func:`run_agent_turn`.

    Aggregates tool calls, LLM steps, and the final assistant string for JSON export.
    """

    tools_called: list[dict[str, Any]] = field(default_factory=list)
    llm_steps: list[dict[str, Any]] = field(default_factory=list)
    response: str | None = None
    _pending_tool: dict[str, Any] | None = None

    def __call__(self, ev: AgentEvent) -> None:
        if isinstance(ev, LlmStepEvent):
            self.llm_steps.append(
                {
                    "step": ev.step,
                    "model": ev.model,
                    "finish_reason": ev.finish_reason,
                    "reasoning": ev.reasoning,
                    "content": ev.content,
                    "tool_intents": ev.tool_intents,
                }
            )
        elif isinstance(ev, ToolCallStarted):
            self._pending_tool = {
                "name": ev.tool_name,
                "arguments": ev.arguments,
                "tool_call_id": ev.tool_call_id,
                "result": None,
                "duration_ms": 0,
            }
        elif isinstance(ev, ToolCallCompleted):
            row = self._pending_tool or {
                "name": ev.tool_name,
                "arguments": {},
                "tool_call_id": ev.tool_call_id,
            }
            row["result"] = ev.result
            row["duration_ms"] = ev.duration_ms
            self.tools_called.append(row)
            self._pending_tool = None
        elif isinstance(ev, ResponseEvent):
            self.response = ev.content


@dataclass
class EvalRunResult:
    """In-memory representation of one full suite run (serialised to JSON)."""

    run_id: str
    started_utc: str
    agent_model: str
    screening_enabled: bool
    questions: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)


def _default_manual_scores() -> dict[str, Any]:
    return {
        "parameter_accuracy": None,
        "synthesis_quality": None,
        "boundary_awareness": None,
        "notes": None,
    }


async def run_eval_suite(
    questions: list[EvalQuestion],
    *,
    model: str | None = None,
    screening: bool = True,
    delay_s: float = 2.0,
    verbose: bool = False,
    show_progress: bool = True,
    event_printer: Callable[[str], None] | None = None,
) -> EvalRunResult:
    """
    Execute each question with a fresh :class:`ConversationMemory`.

    Reuses one HTTP client and one user profile snapshot for speed/consistency.

    When ``show_progress`` is True, prints ``[current/total]`` lines to **stderr** after
    each question (so progress stays visible if stdout is redirected).
    """
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    eff_model = (model or cfg.AGENT_MODEL).strip()
    total_q = len(questions)

    def _progress(msg: str) -> None:
        """User-facing progress (stderr — does not mix with optional stdout piping)."""
        if show_progress:
            print(msg, file=sys.stderr, flush=True)

    def _log(msg: str) -> None:
        if verbose:
            print(msg)
        if event_printer:
            event_printer(msg)

    rows_out: list[dict[str, Any]] = []
    t_wall0 = time.perf_counter()
    # Shared across questions — must exist even if the HTTP client fails early.
    cost_tracker = CostTracker(run_logger=None)
    cost0 = cost_tracker.session_total_usd

    with _agent_model_override(model):
        client = create_agent_http_client()
        try:
            if show_progress:
                _progress("Loading user profile…")
            else:
                _log("Loading user profile…")
            try:
                profile = await generate_user_profile(client)
            except Exception:
                logger.exception("Profile load failed — empty profile")
                profile = "(Profile unavailable — tools may still work.)"
            if show_progress and total_q:
                _progress(
                    f"Eval: {total_q} question(s) | model={eff_model} | "
                    f"screening={'on' if screening else 'off'} | delay={delay_s}s between items"
                )

            for i, q in enumerate(questions):
                if i > 0 and delay_s > 0:
                    await asyncio.sleep(delay_s)

                collector = EvalEventCollector()
                mem = ConversationMemory(max_turns=cfg.MAX_CONVERSATION_TURNS)
                q_t0 = time.perf_counter()
                cost_before = cost_tracker.session_total_usd
                prompt_tokens_before = cost_tracker.session_total_prompt_tokens
                completion_tokens_before = cost_tracker.session_total_completion_tokens

                screening_payload: dict[str, Any] | None = None
                response_text = ""
                agent_ran = False
                err: str | None = None

                try:
                    if screening:
                        sr = await screen_message(q.question, cost_tracker=cost_tracker)
                        screening_payload = {
                            "allowed": sr.allowed,
                            "category": sr.category,
                            "layer": sr.layer,
                            "latency_ms": sr.latency_ms,
                            "rejection_message": sr.rejection_message,
                        }
                        if not sr.allowed:
                            response_text = sr.rejection_message or ""
                        else:
                            agent_ran = True
                            await run_agent_turn(
                                user_message=q.question,
                                memory=mem,
                                client=client,
                                user_profile=profile,
                                event_callback=collector,
                                run_logger=None,
                                cost_tracker=cost_tracker,
                            )
                            response_text = collector.response or ""
                    else:
                        screening_payload = {
                            "allowed": True,
                            "category": None,
                            "layer": "skipped",
                            "latency_ms": 0,
                            "rejection_message": None,
                        }
                        agent_ran = True
                        await run_agent_turn(
                            user_message=q.question,
                            memory=mem,
                            client=client,
                            user_profile=profile,
                            event_callback=collector,
                            run_logger=None,
                            cost_tracker=cost_tracker,
                        )
                        response_text = collector.response or ""

                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    logger.exception("Eval question %s failed", q.id)

                cost_after = cost_tracker.session_total_usd
                prompt_tokens = cost_tracker.session_total_prompt_tokens - prompt_tokens_before
                completion_tokens = (
                    cost_tracker.session_total_completion_tokens - completion_tokens_before
                )
                duration_s = time.perf_counter() - q_t0

                auto = auto_score_question_result(
                    q,
                    screening_enabled=screening,
                    screening_allowed=screening_payload["allowed"] if screening_payload else True,
                    screening_category=(
                        screening_payload.get("category") if screening_payload else None
                    ),
                    tools_called=collector.tools_called,
                    response_text=response_text,
                    agent_ran=agent_ran,
                    error=err,
                )

                row = {
                    "id": q.id,
                    "tier": q.tier,
                    "question": q.question,
                    "expected_behavior": q.expected_behavior,
                    "scoring_notes": q.scoring_notes,
                    "boundary_type": q.boundary_type,
                    "expected_tools": list(q.expected_tools),
                    "tool_match_mode": q.tool_match_mode,
                    "screening": screening_payload,
                    "duration_s": round(duration_s, 3),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd_delta": round(cost_after - cost_before, 8),
                    "tools_called": collector.tools_called,
                    "llm_steps": collector.llm_steps,
                    "response": response_text,
                    "error": err,
                    "manual_scores": _default_manual_scores(),
                    "auto_scores": auto,
                }
                rows_out.append(row)
                _log(
                    f"[{q.id}] auto_overall={'PASS' if auto['overall_pass'] else 'FAIL'} "
                    f"duration={duration_s:.1f}s cost_delta=${cost_after - cost_before:.4f}"
                )
                if show_progress and total_q:
                    done = i + 1
                    passed_so_far = sum(
                        1 for r in rows_out if r.get("auto_scores", {}).get("overall_pass")
                    )
                    if err:
                        tag = "ERR"
                    elif auto["overall_pass"]:
                        tag = "PASS"
                    else:
                        tag = "FAIL"
                    pct = round(100.0 * done / total_q, 1)
                    _progress(
                        f"[{done:>2}/{total_q}] {q.id}  tier {q.tier}  auto={tag}  "
                        f"{duration_s:.1f}s  ${cost_after - cost_before:.4f}  "
                        f"cumulative_auto_pass={passed_so_far}/{done}  ({pct}% complete)"
                    )

        finally:
            await client.aclose()

    total_duration = time.perf_counter() - t_wall0
    total_cost = cost_tracker.session_total_usd - cost0
    auto_passed = sum(1 for r in rows_out if r.get("auto_scores", {}).get("overall_pass"))
    total_prompt_tokens = sum(int(r.get("prompt_tokens", 0) or 0) for r in rows_out)
    total_completion_tokens = sum(int(r.get("completion_tokens", 0) or 0) for r in rows_out)
    totals = {
        "question_count": len(rows_out),
        "auto_pass_count": auto_passed,
        "auto_fail_count": len(rows_out) - auto_passed,
        "wall_duration_s": round(total_duration, 3),
        "total_cost_usd": round(total_cost, 6),
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
    }
    if show_progress and rows_out:
        _progress(
            f"Finished: {auto_passed}/{len(rows_out)} auto-pass | "
            f"est. cost ${round(total_cost, 4)} | {round(total_duration, 1)}s wall clock"
        )
    return EvalRunResult(
        run_id=run_id,
        started_utc=started,
        agent_model=eff_model,
        screening_enabled=screening,
        questions=rows_out,
        totals=totals,
    )


def write_eval_json(result: EvalRunResult, *, path: Path | None = None) -> Path:
    """Serialise ``EvalRunResult`` to ``agent/evals/results/`` (or ``path``)."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        slug = _model_slug_for_filename(result.agent_model)
        fname = f"{slug}__{result.run_id}.json"
        path = _RESULTS_DIR / fname
    payload = asdict(result)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def load_eval_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
