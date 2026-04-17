"""
LiteLLM wrapper — one async entrypoint for chat + tool calls, with fallbacks.
"""

from __future__ import annotations

import logging
from typing import Any

from litellm import acompletion

from agent import config as cfg

logger = logging.getLogger(__name__)


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
    """Optionally pass Gemini 3 ``thinking_level`` via ``extra_body`` (see config flag)."""
    if not cfg.AGENT_GEMINI_EXTRA_THINKING:
        return None
    m = model.lower()
    if "gemini" not in m:
        return None
    return {
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": cfg.AGENT_THINKING_LEVEL.upper()},
        }
    }


async def chat_completion(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> Any:
    """Single chat completion with optional OpenAI-format ``tools``."""
    use_model = model or cfg.AGENT_MODEL
    chain = [use_model] + [m for m in cfg.AGENT_FALLBACK_CHAIN if m != use_model]

    last_err: Exception | None = None
    for m in chain:
        kwargs: dict[str, Any] = {
            "model": m,
            "messages": messages,
            "temperature": cfg.AGENT_TEMPERATURE,
            "max_tokens": cfg.MAX_OUTPUT_TOKENS,
            "timeout": cfg.LLM_REQUEST_TIMEOUT,
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
        extra = _gemini_extra_body(m)
        if extra:
            kwargs["extra_body"] = extra
        try:
            return await acompletion(**kwargs)
        except Exception as e:
            last_err = e
            logger.warning("LLM call failed for model=%s: %s — trying fallback", m, e)
    assert last_err is not None
    raise last_err
