"""
ReAct-style agent loop: the model may call tools until it returns a final answer.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from httpx import AsyncClient

from agent import config as cfg
from agent.events import (
    AgentEvent,
    ErrorEvent,
    LlmStepEvent,
    ResponseEvent,
    ToolCallCompleted,
    ToolCallStarted,
)
from agent.llm import chat_completion
from agent.memory import ConversationMemory
from agent.prompts import load_system_prompt
from agent.run_logger import AgentRunLogger
from agent.sanitizer import sanitize_jsonable
from agent.tools import get_all_tools, get_tool

logger = logging.getLogger(__name__)


def _noop_event(_: AgentEvent) -> None:
    return None


def _serialize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    """Normalize LiteLLM / OpenAI tool call objects to plain dicts."""
    out: list[dict[str, Any]] = []
    if not tool_calls:
        return out
    for tc in tool_calls:
        tid = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None)
        fn = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
        if fn is None:
            continue
        name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else "")
        args = getattr(fn, "arguments", None) or (
            fn.get("arguments") if isinstance(fn, dict) else "{}"
        )
        out.append(
            {
                "id": tid,
                "type": "function",
                "function": {"name": name, "arguments": args or "{}"},
            }
        )
    return out


def _response_model_id(response: Any) -> str | None:
    return getattr(response, "model", None) or getattr(response, "model_id", None)


def _extract_reasoning_from_message(msg: Any) -> str | None:
    """
    Best-effort extraction of chain-of-thought / reasoning text.

    Providers differ; LiteLLM may surface fields on the message object or in
    ``model_extra``.  Empty / missing is normal for models that do not expose it.
    """
    for attr in ("reasoning_content", "reasoning", "thinking"):
        v = getattr(msg, attr, None)
        if v is not None and str(v).strip():
            return str(v).strip()
    extra = getattr(msg, "model_extra", None)
    if isinstance(extra, dict):
        for k in ("reasoning_content", "reasoning", "thinking", "thinking_blocks"):
            v = extra.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
    return None


def _tool_intents_from_serialized(serialized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tc in serialized:
        name = tc.get("function", {}).get("name", "")
        raw = tc.get("function", {}).get("arguments") or "{}"
        try:
            parsed: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else {}
        except json.JSONDecodeError:
            parsed = {"_invalid_json": raw}
        out.append({"name": name, "arguments": parsed})
    return out


async def run_agent_turn(
    *,
    user_message: str,
    memory: ConversationMemory,
    client: AsyncClient,
    user_profile: str,
    event_callback: Callable[[AgentEvent], None] = _noop_event,
    run_logger: AgentRunLogger | None = None,
) -> str:
    """
    Run one user message through the agent.

    1. Append the user message to memory.
    2. Build ``[system] + history`` and enter a tool loop.
    3. On each assistant message with ``tool_calls``, execute tools, sanitize results,
       append tool messages, and call the model again.
    4. On a plain assistant message, append it to memory and return text.

    ``run_logger`` — when set (CLI), append a structured transcript under
    ``agent/logs/`` for later review.
    """
    tools = get_all_tools()
    tool_openai = [t.to_openai_tool() for t in tools]
    system_prompt = load_system_prompt(user_profile=user_profile, tools=tools)

    memory.add_user_message(user_message)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *memory.get_messages(),
    ]

    if run_logger is not None:
        run_logger.log_user_message(user_message)

    turn_fragments: list[dict[str, Any]] = []
    tool_batches = 0
    llm_step = 0

    while True:
        llm_step += 1
        response = await chat_completion(messages=messages, tools=tool_openai)
        choice = response.choices[0]
        msg = choice.message
        finish = str(choice.finish_reason or "").strip().lower()
        model_used = _response_model_id(response)
        raw_content = getattr(msg, "content", None)
        content: str | None
        if isinstance(raw_content, str):
            content = raw_content
        elif raw_content is not None:
            content = json.dumps(raw_content, ensure_ascii=False, default=str)
        else:
            content = None
        reasoning = _extract_reasoning_from_message(msg)

        tcalls = getattr(msg, "tool_calls", None)
        if tcalls or finish == "tool_calls":
            tool_batches += 1
            if tool_batches > cfg.MAX_TOOL_CALLS_PER_TURN:
                break
            serialized = _serialize_tool_calls(tcalls)
            tool_intents = _tool_intents_from_serialized(serialized)

            if run_logger is not None:
                run_logger.log_llm_step(
                    step=llm_step,
                    model=model_used,
                    finish_reason=finish or None,
                    content=content,
                    reasoning=reasoning,
                    tool_intents=tool_intents,
                )
            event_callback(
                LlmStepEvent(
                    step=llm_step,
                    model=model_used,
                    finish_reason=finish or None,
                    content=content,
                    reasoning=reasoning,
                    tool_intents=tool_intents,
                )
            )

            if not serialized:
                logger.warning("finish_reason=%s but no tool_calls parsed — stopping", finish)
                text = getattr(msg, "content", None) or (
                    "The model returned an unreadable tool call. Please try again."
                )
                final_assistant = {"role": "assistant", "content": text}
                messages.append(final_assistant)
                turn_fragments.append(final_assistant)
                memory.extend_messages(turn_fragments)
                event_callback(ResponseEvent(content=text))
                if run_logger is not None:
                    run_logger.log_note("unreadable tool_calls — returning apology text")
                    run_logger.log_final_assistant(text)
                return text
            assistant_part: dict[str, Any] = {
                "role": "assistant",
                "content": getattr(msg, "content", None),
                "tool_calls": serialized,
            }
            messages.append(assistant_part)
            turn_fragments.append(assistant_part)

            for tc in serialized:
                t0 = time.perf_counter()
                name = tc["function"]["name"]
                args = tc["function"]["arguments"]
                tid = tc.get("id") or ""
                try:
                    parsed_args: dict[str, Any] = json.loads(args or "{}")
                except json.JSONDecodeError:
                    parsed_args = {"_invalid_json": args}
                event_callback(
                    ToolCallStarted(
                        tool_name=name,
                        arguments=parsed_args,
                        tool_call_id=tid or None,
                    )
                )
                spec = get_tool(name)
                if spec is None:
                    payload = {"status": "error", "error": "unknown_tool", "detail": name}
                else:
                    payload = await spec.execute(client, args)
                safe = sanitize_jsonable(payload)
                body = json.dumps(safe, ensure_ascii=False)
                dur_ms = int((time.perf_counter() - t0) * 1000)
                event_callback(
                    ToolCallCompleted(
                        tool_name=name,
                        result=safe,
                        duration_ms=dur_ms,
                        tool_call_id=tid or None,
                    )
                )
                if run_logger is not None:
                    run_logger.log_tool_result(
                        tool_name=name,
                        arguments=parsed_args,
                        result=safe,
                        duration_ms=dur_ms,
                    )
                tool_msg = {"role": "tool", "tool_call_id": tid, "content": body}
                messages.append(tool_msg)
                turn_fragments.append(tool_msg)

            continue

        # Final natural-language reply (no tool_calls on this completion)
        if run_logger is not None:
            run_logger.log_llm_step(
                step=llm_step,
                model=model_used,
                finish_reason=finish or None,
                content=content,
                reasoning=reasoning,
                tool_intents=[],
            )
        event_callback(
            LlmStepEvent(
                step=llm_step,
                model=model_used,
                finish_reason=finish or None,
                content=content,
                reasoning=reasoning,
                tool_intents=[],
            )
        )

        text = content if content is not None else ""
        final_assistant = {"role": "assistant", "content": text}
        messages.append(final_assistant)
        turn_fragments.append(final_assistant)
        memory.extend_messages(turn_fragments)
        event_callback(ResponseEvent(content=text))
        if run_logger is not None:
            run_logger.log_final_assistant(text)
        return text

    apology = (
        "I hit the tool-call safety limit for this question. "
        "Try narrowing the question (one time period or one account) and ask again."
    )
    event_callback(ErrorEvent(message=apology, recoverable=True))
    if turn_fragments:
        memory.extend_messages(turn_fragments)
    memory.extend_messages([{"role": "assistant", "content": apology}])
    if run_logger is not None:
        run_logger.log_note("MAX_TOOL_CALLS_PER_TURN exceeded")
        run_logger.log_final_assistant(apology)
    return apology
