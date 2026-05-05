"""
ReAct-style agent loop: the model may call tools until it returns a final answer.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

from agent import config as cfg
from agent.events import (
    AgentEvent,
    ErrorEvent,
    LlmStepEvent,
    ResponseEvent,
    ThinkingDoneEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallCompleted,
    ToolCallStarted,
)
from agent.llm import chat_completion, streaming_chat_completion
from agent.memory import ConversationMemory
from agent.prompts import load_system_prompt
from agent.run_logger import AgentRunLogger
from agent.security.output_sanitizer import wrap_tool_output
from agent.sanitizer import sanitize_jsonable
from agent.tools import get_all_tools, get_tool

logger = logging.getLogger(__name__)

# Shown when the user has sent the maximum number of user turns for this session.
# Kept in one place so the CLI pre-check and ``run_agent_turn`` stay in sync.
CONVERSATION_LIMIT_REPLY = (
    "You've hit the limit for this chat — start a fresh one and we can keep going."
)


def _noop_event(_: AgentEvent) -> None:
    return None


# Callback may be sync (CLI, evals) or async (dashboard WebSocket).
AgentEventCallback = Callable[[AgentEvent], None | Awaitable[None]]


async def _emit_event(cb: AgentEventCallback, ev: AgentEvent) -> None:
    """Invoke the subscriber; await if it returns a coroutine (async dashboard UI)."""
    result = cb(ev)
    if inspect.isawaitable(result):
        await result  # type: ignore[arg-type]


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


def _final_text_assistant_index(fragments: list[dict[str, Any]]) -> int | None:
    """Index of the last assistant message without tool_calls (natural-language reply)."""
    for j in range(len(fragments) - 1, -1, -1):
        m = fragments[j]
        if m.get("role") != "assistant":
            continue
        if m.get("tool_calls"):
            continue
        return j
    return None


def _thinking_joined_from_timeline(timeline: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for seg in timeline:
        if str(seg.get("kind")) != "thinking":
            continue
        c = seg.get("content")
        if isinstance(c, str) and c.strip():
            parts.append(c.strip())
    return "\n\n---\n\n".join(parts)


def _attach_arth_metadata_to_final_assistant(
    fragments: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    thinking_parts_fallback: list[str],
) -> None:
    """Persist chronological thinking/tool segments on the final assistant row (dashboard UI)."""
    idx = _final_text_assistant_index(fragments)
    if idx is None:
        return
    msg = fragments[idx]
    if timeline:
        msg["_arth_timeline"] = timeline
        joined = _thinking_joined_from_timeline(timeline)
        if joined.strip():
            msg["_arth_thinking"] = joined
        return
    combined = "\n\n---\n\n".join(p for p in thinking_parts_fallback if p and str(p).strip())
    if combined.strip():
        msg["_arth_thinking"] = combined


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
    event_callback: AgentEventCallback = _noop_event,
    run_logger: AgentRunLogger | None = None,
    cost_tracker: Any | None = None,
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

    ``cost_tracker`` — when set, records LiteLLM ``usage`` + estimated USD per completion.
    """
    if memory.turn_count() >= cfg.MAX_CONVERSATION_TURNS:
        text = CONVERSATION_LIMIT_REPLY
        if run_logger is not None:
            run_logger.log_user_message(user_message)
            run_logger.log_note("MAX_CONVERSATION_TURNS reached — user message not stored in memory")
        await _emit_event(event_callback, ResponseEvent(content=text))
        return text

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
        # Exact system role content the API receives (profile + tool list inlined).
        run_logger.log_final_system_prompt_once(system_prompt)

    turn_fragments: list[dict[str, Any]] = []
    turn_reasoning_parts: list[str] = []
    # Ordered segments: {"kind": "thinking"|"tools", ...} — chronological ReAct trace for the UI.
    turn_timeline: list[dict[str, Any]] = []
    tool_batches = 0
    llm_step = 0

    while True:
        llm_step += 1
        step_thinking_buf = ""
        # Prefer streaming so the dashboard can render token deltas. Tool-call
        # turns suppress text emission as soon as tool deltas appear (see ``llm``).
        stream_emitted = False

        async def _emit_token(delta: str) -> None:
            nonlocal stream_emitted
            stream_emitted = True
            await _emit_event(event_callback, TokenEvent(token=delta))

        async def _emit_thinking_delta(delta: str) -> None:
            nonlocal step_thinking_buf
            step_thinking_buf += delta
            await _emit_event(event_callback, ThinkingEvent(content=delta))

        async def _emit_thinking_done() -> None:
            await _emit_event(event_callback, ThinkingDoneEvent())

        thinking_chunks_were_streamed = False
        try:
            response, thinking_chunks_were_streamed = await streaming_chat_completion(
                messages=messages,
                tools=tool_openai,
                cost_tracker=cost_tracker,
                usage_call_type="agent",
                on_text_delta=_emit_token,
                on_thinking_delta=_emit_thinking_delta,
                on_thinking_done=_emit_thinking_done,
            )
        except Exception as e:
            logger.warning(
                "Streaming completion failed (%s) — falling back to non-streaming",
                e,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            if stream_emitted:
                # Partial tokens may already be on the wire; a second completion would duplicate.
                raise
            response = await chat_completion(
                messages=messages,
                tools=tool_openai,
                cost_tracker=cost_tracker,
                usage_call_type="agent",
            )
        choice = response.choices[0]
        msg = choice.message
        finish = str(choice.finish_reason or "").strip().lower()
        model_used = _response_model_id(response)
        # Correlates with ``agent.llm`` DEBUG lines (latency_ms + tokens) for this HTTP round-trip.
        logger.debug(
            "Agent loop after LLM step=%s model=%s finish_reason=%s",
            llm_step,
            model_used,
            finish or None,
        )
        raw_content = getattr(msg, "content", None)
        content: str | None
        if isinstance(raw_content, str):
            content = raw_content
        elif raw_content is not None:
            content = json.dumps(raw_content, ensure_ascii=False, default=str)
        else:
            content = None
        reasoning = _extract_reasoning_from_message(msg)
        if reasoning:
            turn_reasoning_parts.append(reasoning.strip())
        if not thinking_chunks_were_streamed and reasoning:
            await _emit_event(event_callback, ThinkingEvent(content=reasoning))
            await _emit_event(event_callback, ThinkingDoneEvent())

        step_thinking_for_timeline = step_thinking_buf.strip()
        if not step_thinking_for_timeline and reasoning:
            step_thinking_for_timeline = str(reasoning).strip()
        if step_thinking_for_timeline:
            turn_timeline.append({"kind": "thinking", "content": step_thinking_for_timeline})

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
            await _emit_event(
                event_callback,
                LlmStepEvent(
                    step=llm_step,
                    model=model_used,
                    finish_reason=finish or None,
                    content=content,
                    reasoning=reasoning,
                    tool_intents=tool_intents,
                ),
            )

            if not serialized:
                logger.warning("finish_reason=%s but no tool_calls parsed — stopping", finish)
                text = getattr(msg, "content", None) or (
                    "The model returned an unreadable tool call. Please try again."
                )
                final_assistant = {"role": "assistant", "content": text}
                messages.append(final_assistant)
                turn_fragments.append(final_assistant)
                _attach_arth_metadata_to_final_assistant(
                    turn_fragments, turn_timeline, turn_reasoning_parts
                )
                memory.extend_messages(turn_fragments)
                await _emit_event(event_callback, ResponseEvent(content=text))
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

            tools_for_timeline: list[dict[str, Any]] = []
            for tc in serialized:
                t0 = time.perf_counter()
                name = tc["function"]["name"]
                args = tc["function"]["arguments"]
                tid = tc.get("id") or ""
                try:
                    parsed_args: dict[str, Any] = json.loads(args or "{}")
                except json.JSONDecodeError:
                    parsed_args = {"_invalid_json": args}
                # Visible at DEBUG: ties Datadog/console traces to UI ToolCallStarted events.
                logger.debug(
                    "Tool invocation start step=%s tool=%s tool_call_id=%s",
                    llm_step,
                    name,
                    tid or "(none)",
                )
                await _emit_event(
                    event_callback,
                    ToolCallStarted(
                        tool_name=name,
                        arguments=parsed_args,
                        tool_call_id=tid or None,
                    ),
                )
                spec = get_tool(name)
                if spec is None:
                    payload = {"status": "error", "error": "unknown_tool", "detail": name}
                else:
                    payload = await spec.execute(client, args)
                safe = sanitize_jsonable(payload)
                if not isinstance(safe, dict):
                    safe = {"status": "success", "data": safe}
                body = wrap_tool_output(name, safe)
                dur_ms = int((time.perf_counter() - t0) * 1000)
                tool_outcome = "unknown_tool" if spec is None else "ok"
                # One line per tool at INFO — enough to profile slow tools without DEBUG spam from tokens.
                logger.info(
                    "Tool invocation finished step=%s tool=%s duration_ms=%s outcome=%s",
                    llm_step,
                    name,
                    dur_ms,
                    tool_outcome,
                )
                tools_for_timeline.append(
                    {
                        "name": name,
                        "arguments": parsed_args,
                        "result": safe,
                        "duration_ms": dur_ms,
                    }
                )
                await _emit_event(
                    event_callback,
                    ToolCallCompleted(
                        tool_name=name,
                        result=safe,
                        duration_ms=dur_ms,
                        tool_call_id=tid or None,
                    ),
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

            if tools_for_timeline:
                turn_timeline.append({"kind": "tools", "tools": tools_for_timeline})

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
        await _emit_event(
            event_callback,
            LlmStepEvent(
                step=llm_step,
                model=model_used,
                finish_reason=finish or None,
                content=content,
                reasoning=reasoning,
                tool_intents=[],
            ),
        )

        text = content if content is not None else ""
        final_assistant = {"role": "assistant", "content": text}
        messages.append(final_assistant)
        turn_fragments.append(final_assistant)
        _attach_arth_metadata_to_final_assistant(turn_fragments, turn_timeline, turn_reasoning_parts)
        memory.extend_messages(turn_fragments)
        await _emit_event(event_callback, ResponseEvent(content=text))
        if run_logger is not None:
            run_logger.log_final_assistant(text)
        return text

    apology = (
        "I hit the tool-call safety limit for this question. "
        "Try narrowing the question (one time period or one account) and ask again."
    )
    await _emit_event(event_callback, ErrorEvent(message=apology, recoverable=True))
    if turn_fragments:
        memory.extend_messages(turn_fragments)
    memory.extend_messages([{"role": "assistant", "content": apology}])
    if run_logger is not None:
        run_logger.log_note("MAX_TOOL_CALLS_PER_TURN exceeded")
        run_logger.log_final_assistant(apology)
    return apology
