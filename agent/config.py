"""
Agent-specific configuration (models, limits, feature flags).

Kept separate from ``pipeline/config.py`` — the pipeline uses raw SDKs and its
own model map; the agent uses LiteLLM with ``provider/model`` strings.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths (for prompt files, future cloud-fetch swap)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# ---------------------------------------------------------------------------
# Internal ASGI auth — must match ``api.auth.get_current_user`` header check
# ---------------------------------------------------------------------------
# Lazy resolution: importing ``api.auth`` pulls ``bcrypt``. Agent unit tests only
# need screening/config constants, so we defer that import until a client asks
# for the token (see ``get_internal_auth_token``).
_internal_auth_token_cache: str | None = None


def get_internal_auth_token() -> str:
    """Return the shared secret for ``X-Arth-Internal`` (in-process agent → API)."""
    global _internal_auth_token_cache
    if _internal_auth_token_cache is None:
        from api.auth import agent_internal_token

        _internal_auth_token_cache = agent_internal_token()
    return _internal_auth_token_cache

# ---------------------------------------------------------------------------
# LLM — Gemini 3 defaults per Google guidance (temperature 1.0)
# ---------------------------------------------------------------------------
# LiteLLM model strings: https://docs.litellm.ai/docs/providers
AGENT_MODEL: str = os.getenv("AGENT_MODEL", "gemini/gemini-3-flash-preview")

AGENT_FALLBACK_CHAIN: list[str] = [
    m.strip()
    for m in os.getenv(
        "AGENT_FALLBACK_CHAIN",
        "gemini/gemini-3-flash-preview,anthropic/claude-sonnet-4-6,openai/gpt-5.4-mini",
    ).split(",")
    if m.strip()
]

# Layer-2 input screening (cheap classifier; separate chain from main agent).
SCREENING_MODEL: str = os.getenv(
    "SCREENING_MODEL", "gemini/gemini-3.1-flash-lite-preview"
)
SCREENING_FALLBACK_CHAIN: list[str] = [
    m.strip()
    for m in os.getenv(
        "SCREENING_FALLBACK_CHAIN",
        "gemini/gemini-3.1-flash-lite-preview,gemini/gemini-2.5-flash-lite,anthropic/claude-haiku-4-5",
    ).split(",")
    if m.strip()
]
SCREENING_TEMPERATURE: float = float(os.getenv("SCREENING_TEMPERATURE", "0.0"))
SCREENING_MAX_TOKENS: int = int(os.getenv("SCREENING_MAX_TOKENS", "32"))
SCREENING_TIMEOUT: float = float(os.getenv("SCREENING_TIMEOUT", "10"))
SCREENING_ENABLED: bool = os.getenv("SCREENING_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

AGENT_TEMPERATURE: float = float(os.getenv("AGENT_TEMPERATURE", "1.0"))
MAX_OUTPUT_TOKENS: int = int(os.getenv("MAX_OUTPUT_TOKENS", "24576"))
MAX_TOOL_CALLS_PER_TURN: int = int(os.getenv("MAX_TOOL_CALLS_PER_TURN", "10"))
MAX_CONVERSATION_TURNS: int = int(os.getenv("MAX_CONVERSATION_TURNS", "40"))

# Rate limiting (messages per minute per CLI session / future WebSocket session).
RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# Estimated LLM spend alert (in-process daily counter; resets when process crosses UTC day).
COST_ALERT_DAILY_USD: float = float(os.getenv("COST_ALERT_DAILY_USD", "5.0"))

# Gemini 3 thinking depth: high | medium | low | minimal (passed via extra_body when enabled).
AGENT_THINKING_LEVEL: str = os.getenv("AGENT_THINKING_LEVEL", "high").strip().lower()
# Off by default — some LiteLLM + Google stacks reject unknown generationConfig keys.
AGENT_GEMINI_EXTRA_THINKING: bool = os.getenv(
    "AGENT_GEMINI_EXTRA_THINKING", "false"
).strip().lower() in ("1", "true", "yes")

# OpenAI / Anthropic-style reasoning effort (LiteLLM ``reasoning_effort``). Empty = off.
# Values: low | medium | high — extra output tokens / cost when enabled.
AGENT_REASONING_EFFORT: str = os.getenv("AGENT_REASONING_EFFORT", "").strip().lower()

# Request timeout for each LLM call (seconds)
LLM_REQUEST_TIMEOUT: float = float(os.getenv("LLM_REQUEST_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# Agent LLM keys — **only** *_FOR_SINGLE_AGENT (no fallback to classifier keys)
# so provider dashboards bill/trace the agent separately from classification.
# ---------------------------------------------------------------------------
AGENT_OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY_FOR_SINGLE_AGENT", "").strip()
AGENT_ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY_FOR_SINGLE_AGENT", "").strip()
AGENT_GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY_FOR_SINGLE_AGENT", "").strip()

# ---------------------------------------------------------------------------
# Cost estimation (USD per 1M tokens) — LiteLLM ``model`` id keys.
# Update when provider list prices change; used only for logging / soft alerts.
# ---------------------------------------------------------------------------
AGENT_MODEL_PRICING: dict[str, dict[str, float]] = {
    # Google Gemini (paid tier, per 1M tokens USD)
    "gemini/gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "gemini/gemini-3.1-flash-lite-preview": {"input": 0.25, "output": 1.50},
    "gemini/gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini/gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    # Anthropic Claude (base input, per 1M tokens USD)
    "anthropic/claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "anthropic/claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "anthropic/claude-opus-4-7": {"input": 5.00, "output": 25.00},
    "anthropic/claude-opus-4-6": {"input": 5.00, "output": 25.00},
    "anthropic/claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "anthropic/claude-haiku-3-5": {"input": 0.80, "output": 4.00},
    # OpenAI GPT (short context, per 1M tokens USD)
    "openai/gpt-5-mini-2025-08-07": {"input": 0.25, "output": 2.00},
    "openai/gpt-5-nano-2025-08-07": {"input": 0.05, "output": 0.40},
    "openai/gpt-5.4": {"input": 2.50, "output": 15.00},
    "openai/gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "openai/gpt-5.4-nano": {"input": 0.20, "output": 1.25},
    "openai/gpt-5.4-pro": {"input": 30.00, "output": 270.00},
}
