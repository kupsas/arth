"""
LiteLLM wrapper — one async entrypoint for chat + tool calls, with fallbacks.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import litellm
from litellm import acompletion, stream_chunk_builder

from agent import config as cfg

# Must run after ``import litellm`` (library reads this flag at call time).
litellm.suppress_debug_info = True
# Anthropic thinking + tool calls: LiteLLM can drop incompatible params automatically.
litellm.modify_params = True

logger = logging.getLogger(__name__)

# Stripped before every provider call — stored in RAM/SQLite for UI + replay only.
_ARTH_PERSIST_KEYS = frozenset({"_arth_thinking", "_arth_timeline"})


def _usage_tokens_snapshot(response: Any) -> tuple[int, int, int]:
    """
    Pull token counts from a LiteLLM / OpenAI-shaped response for structured logs.

    Mirrors :func:`agent.security.cost_tracker._usage_tokens` so LLM logging stays
    self-contained (no import cycle) while staying consistent with billing math.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    pt = int(getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None) or 0)
    ct = int(
        getattr(usage, "completion_tokens", None)
        or getattr(usage, "output_tokens", None)
        or 0
    )
    tt = int(getattr(usage, "total_tokens", None) or (pt + ct))
    return pt, ct, tt


def _response_finish_reason(response: Any) -> str | None:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None
    fr = getattr(choices[0], "finish_reason", None)
    return str(fr) if fr is not None else None


def _log_llm_roundtrip_debug(
    *,
    usage_call_type: str,
    model_attempt: str,
    latency_ms: int,
    response: Any,
    streaming: bool,
) -> None:
    """
    Emit latency + usage metadata for each successful provider round-trip.

    Kept at DEBUG so normal runs stay quiet; turn on DEBUG when profiling latency
    or correlating token usage with slow steps.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    pt, ct, tt = _usage_tokens_snapshot(response)
    rid = getattr(response, "model", None) or getattr(response, "model_id", None)
    finish = _response_finish_reason(response)
    logger.debug(
        "LLM round-trip usage_call_type=%r model_attempt=%r response_model=%r "
        "streaming=%s latency_ms=%s finish_reason=%r prompt_tokens=%s "
        "completion_tokens=%s total_tokens=%s",
        usage_call_type,
        model_attempt,
        rid,
        streaming,
        latency_ms,
        finish,
        pt,
        ct,
        tt,
    )


def messages_for_llm(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove Arth-only keys from chat rows before sending to LiteLLM."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if not any(k in m for k in _ARTH_PERSIST_KEYS):
            out.append(m)
            continue
        out.append({k: v for k, v in m.items() if k not in _ARTH_PERSIST_KEYS})
    return out


def _api_key_for_litellm_model(model: str) -> str | None:
    """Resolve the agent's provider key for this LiteLLM model id (no shared env fallbacks)."""
    m = model.strip().lower()
    if m.startswith("openai/") or m.startswith("azure/"):
        return cfg.AGENT_OPENAI_API_KEY or None
    if m.startswith("anthropic/"):
        return cfg.AGENT_ANTHROPIC_API_KEY or None
    if m.startswith("gemini/") or m.startswith("google/") or m.startswith("vertex_ai/"):
        return cfg.AGENT_GOOGLE_API_KEY or None
    # Bare model ids (e.g. from env override) — best-effort routing
    if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return cfg.AGENT_OPENAI_API_KEY or None
    if m.startswith("claude"):
        return cfg.AGENT_ANTHROPIC_API_KEY or None
    logger.warning("Unknown LiteLLM model prefix %r — no api_key injected", model)
    return None


def _model_expects_agent_api_key(model: str) -> bool:
    """True when this model id clearly needs one of the *_FOR_SINGLE_AGENT keys."""
    m = model.strip().lower()
    return bool(
        m.startswith(
            ("openai/", "anthropic/", "gemini/", "google/", "vertex_ai/", "azure/")
        )
        or m.startswith("gpt-")
        or m.startswith("claude")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
    )


def _gemini_extra_body(model: str) -> dict[str, Any] | None:
    """
    Optionally pass Gemini ``thinkingConfig`` via ``extra_body`` (see config flag).

    ``includeThoughts`` matches Google’s “thought summaries” API — without it, the model
    may still reason internally, but LiteLLM often only exposes thought text on the
    **merged** response, so our stream sees no per-chunk ``delta.reasoning_content``.
    See: https://ai.google.dev/gemini-api/docs/thinking (streaming thought summaries).
    """
    if not cfg.AGENT_GEMINI_EXTRA_THINKING:
        return None
    m = model.lower()
    if "gemini" not in m:
        return None
    return {
        "generationConfig": {
            "thinkingConfig": {
                "thinkingLevel": cfg.AGENT_THINKING_LEVEL.upper(),
                "includeThoughts": True,
            },
        }
    }


async def chat_completion(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    fallback_chain: list[str] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    cost_tracker: Any | None = None,
    usage_call_type: str = "agent",
) -> Any:
    """
    Single chat completion with optional OpenAI-format ``tools``.

    ``fallback_chain`` — when set, replaces :data:`agent.config.AGENT_FALLBACK_CHAIN`
    (used for screening). Primary model is ``model`` or :data:`agent.config.AGENT_MODEL`.

    ``cost_tracker`` — when set, records token usage + estimated USD after a successful call.
    """
    temp = cfg.AGENT_TEMPERATURE if temperature is None else float(temperature)
    mtok = cfg.MAX_OUTPUT_TOKENS if max_tokens is None else int(max_tokens)
    tout = cfg.LLM_REQUEST_TIMEOUT if timeout is None else float(timeout)

    primary = model or cfg.AGENT_MODEL
    tail = cfg.AGENT_FALLBACK_CHAIN if fallback_chain is None else fallback_chain
    chain = [primary] + [m for m in tail if m != primary]

    api_messages = messages_for_llm(messages)
    last_err: Exception | None = None
    for m in chain:
        kwargs: dict[str, Any] = {
            "model": m,
            "messages": api_messages,
            "temperature": temp,
            "max_tokens": mtok,
            "timeout": tout,
        }
        ak = _api_key_for_litellm_model(m)
        if ak:
            kwargs["api_key"] = ak
        elif _model_expects_agent_api_key(m):
            raise RuntimeError(
                f"Agent model {m!r} needs an API key. Set one of "
                "OPENAI_API_KEY_FOR_SINGLE_AGENT, ANTHROPIC_API_KEY_FOR_SINGLE_AGENT, "
                "GOOGLE_API_KEY_FOR_SINGLE_AGENT in the root .env (separate from classification keys)."
            )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        # Thinking knobs apply only to main agent completions (not tiny screening calls).
        if usage_call_type == "agent":
            extra = _gemini_extra_body(m)
            if extra:
                kwargs["extra_body"] = extra
            if cfg.AGENT_REASONING_EFFORT:
                kwargs["reasoning_effort"] = cfg.AGENT_REASONING_EFFORT
        try:
            t0 = time.perf_counter()
            resp = await acompletion(**kwargs)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            _log_llm_roundtrip_debug(
                usage_call_type=usage_call_type,
                model_attempt=m,
                latency_ms=latency_ms,
                response=resp,
                streaming=False,
            )
            if cost_tracker is not None:
                cost_tracker.record_litellm_response(
                    response=resp, call_type=usage_call_type, model=m
                )
            return resp
        except Exception as e:
            last_err = e
            logger.warning("LLM call failed for model=%s: %s — trying fallback", m, e)
    assert last_err is not None
    raise last_err


# --- Streaming (final assistant text only; see ``agent.core``) ----------------


async def _invoke_text_delta(
    on_text_delta: Callable[[str], None | Awaitable[None]] | None,
    delta: str,
) -> None:
    """Call subscriber for each streamed text slice; await if it returns a coroutine."""
    if not on_text_delta or not delta:
        return
    out = on_text_delta(delta)
    if inspect.isawaitable(out):
        await out  # type: ignore[misc]


async def _invoke_thinking_delta(
    on_thinking_delta: Callable[[str], None | Awaitable[None]] | None,
    delta: str,
) -> None:
    if not on_thinking_delta or not delta:
        return
    out = on_thinking_delta(delta)
    if inspect.isawaitable(out):
        await out  # type: ignore[misc]


async def _invoke_thinking_done(
    on_thinking_done: Callable[[], None | Awaitable[None]] | None,
) -> None:
    if not on_thinking_done:
        return
    out = on_thinking_done()
    if inspect.isawaitable(out):
        await out  # type: ignore[misc]


def _chunk_has_tool_delta(chunk: Any) -> bool:
    """True when this stream chunk carries tool-call fragments (must not treat as final text)."""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return False
    c0 = choices[0]
    delta = getattr(c0, "delta", None)
    if delta is None:
        return False
    tc = getattr(delta, "tool_calls", None)
    if tc:
        return True
    if isinstance(delta, dict) and delta.get("tool_calls"):
        return True
    return False


def _chunk_text_delta(chunk: Any) -> str:
    """Best-effort assistant text delta from one LiteLLM / OpenAI stream chunk."""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    c0 = choices[0]
    delta = getattr(c0, "delta", None)
    if delta is None:
        return ""
    raw = getattr(delta, "content", None)
    if raw is None and isinstance(delta, dict):
        raw = delta.get("content")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        # Some providers send content as a list of typed parts (e.g. multimodal).
        parts: list[str] = []
        for p in raw:
            if isinstance(p, dict) and p.get("type") == "text":
                t = p.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    return str(raw)


def _chunk_thinking_delta(chunk: Any) -> str:
    """
    Reasoning / thinking delta from one LiteLLM streaming chunk (best-effort).

    Anthropic exposes ``reasoning_content`` on the delta. Gemini (via LiteLLM) maps
    Gemini parts with ``thought: true`` onto ``delta.reasoning_content`` per chunk
    when thought summaries are enabled — same field, so one code path covers both.
    """
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    c0 = choices[0]
    delta = getattr(c0, "delta", None)
    if delta is None:
        return ""
    if isinstance(delta, dict):
        for k in ("reasoning_content", "reasoning", "thinking"):
            v = delta.get(k)
            if isinstance(v, str) and v:
                return v
        mex = delta.get("model_extra")
        if isinstance(mex, dict):
            for k in ("reasoning_content", "reasoning", "thinking"):
                v = mex.get(k)
                if isinstance(v, str) and v:
                    return v
        return ""
    for attr in ("reasoning_content", "reasoning", "thinking"):
        v = getattr(delta, attr, None)
        if isinstance(v, str) and v:
            return v
    extra = getattr(delta, "model_extra", None)
    if isinstance(extra, dict):
        for k in ("reasoning_content", "reasoning", "thinking"):
            v = extra.get(k)
            if isinstance(v, str) and v:
                return v
    return ""


async def streaming_chat_completion(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    fallback_chain: list[str] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    cost_tracker: Any | None = None,
    usage_call_type: str = "agent",
    on_text_delta: Callable[[str], None | Awaitable[None]] | None = None,
    on_thinking_delta: Callable[[str], None | Awaitable[None]] | None = None,
    on_thinking_done: Callable[[], None | Awaitable[None]] | None = None,
) -> tuple[Any, bool]:
    """
    Stream one chat completion; invoke ``on_text_delta`` for each safe text slice.

    Same kwargs shape as :func:`chat_completion`. Uses only the **primary** model
    from the chain (no streaming fallback chain) — callers that need fallbacks should
    catch exceptions and call :func:`chat_completion` without streaming.

    If the model streams tool-call deltas, we stop emitting text immediately, drain
    the stream, and return the reconstructed full response (same shape as non-stream)
    so the agent loop can branch into the tool path.

    ``on_thinking_delta`` / ``on_thinking_done`` — optional reasoning stream (e.g.
    Anthropic extended thinking). ``on_thinking_done`` runs when the stream moves
    from reasoning to tool calls or visible assistant text.

    Returns ``(full_response, thinking_chunks_were_streamed)``.

    ``cost_tracker`` — after the stream finishes, usage is taken from the merged
    response via ``stream_chunk_builder`` (same as non-stream path).
    """
    temp = cfg.AGENT_TEMPERATURE if temperature is None else float(temperature)
    mtok = cfg.MAX_OUTPUT_TOKENS if max_tokens is None else int(max_tokens)
    tout = cfg.LLM_REQUEST_TIMEOUT if timeout is None else float(timeout)
    primary = model or cfg.AGENT_MODEL
    tail = cfg.AGENT_FALLBACK_CHAIN if fallback_chain is None else fallback_chain
    chain = [primary] + [m for m in tail if m != primary]
    m = chain[0]
    api_messages = messages_for_llm(messages)

    kwargs: dict[str, Any] = {
        "model": m,
        "messages": api_messages,
        "temperature": temp,
        "max_tokens": mtok,
        "timeout": tout,
        "stream": True,
    }
    # Usage on the final chunk (OpenAI-compatible providers).
    kwargs["stream_options"] = {"include_usage": True}

    ak = _api_key_for_litellm_model(m)
    if ak:
        kwargs["api_key"] = ak
    elif _model_expects_agent_api_key(m):
        raise RuntimeError(
            f"Agent model {m!r} needs an API key. Set one of "
            "OPENAI_API_KEY_FOR_SINGLE_AGENT, ANTHROPIC_API_KEY_FOR_SINGLE_AGENT, "
            "GOOGLE_API_KEY_FOR_SINGLE_AGENT in the root .env (separate from classification keys)."
        )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if usage_call_type == "agent":
        extra = _gemini_extra_body(m)
        if extra:
            kwargs["extra_body"] = extra
        if cfg.AGENT_REASONING_EFFORT:
            kwargs["reasoning_effort"] = cfg.AGENT_REASONING_EFFORT

    chunks: list[Any] = []
    saw_tool_delta = False
    think_streamed = False
    thinking_done_emitted = False

    stream_t0 = time.perf_counter()
    try:
        stream = await acompletion(**kwargs)
    except TypeError:
        # Older providers / LiteLLM builds may reject ``stream_options``.
        kwargs.pop("stream_options", None)
        stream = await acompletion(**kwargs)

    async for chunk in stream:
        chunks.append(chunk)
        th = _chunk_thinking_delta(chunk)
        has_tool = _chunk_has_tool_delta(chunk)
        tx = _chunk_text_delta(chunk)

        if th:
            think_streamed = True
            await _invoke_thinking_delta(on_thinking_delta, th)

        if think_streamed and not thinking_done_emitted and on_thinking_done:
            if has_tool or (bool(tx and tx.strip())):
                await _invoke_thinking_done(on_thinking_done)
                thinking_done_emitted = True

        if has_tool:
            saw_tool_delta = True
            continue
        if not saw_tool_delta and tx:
            await _invoke_text_delta(on_text_delta, tx)

    if think_streamed and on_thinking_done and not thinking_done_emitted:
        await _invoke_thinking_done(on_thinking_done)

    full = stream_chunk_builder(chunks, messages=api_messages)
    if full is None:
        raise RuntimeError("streaming_chat_completion: stream_chunk_builder returned None")

    stream_latency_ms = int((time.perf_counter() - stream_t0) * 1000)
    _log_llm_roundtrip_debug(
        usage_call_type=usage_call_type,
        model_attempt=m,
        latency_ms=stream_latency_ms,
        response=full,
        streaming=True,
    )

    if cost_tracker is not None:
        cost_tracker.record_litellm_response(
            response=full, call_type=usage_call_type, model=m
        )
    return full, think_streamed
