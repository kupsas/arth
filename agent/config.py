"""
Agent-specific configuration (models, limits, feature flags).

Kept separate from ``pipeline/config.py`` — the pipeline uses raw SDKs and its
own model map; the agent uses LiteLLM with ``provider/model`` strings.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from api.auth import agent_internal_token

load_dotenv()

# ---------------------------------------------------------------------------
# Paths (for prompt files, future cloud-fetch swap)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# ---------------------------------------------------------------------------
# Internal ASGI auth — must match ``api.auth.get_current_user`` header check
# ---------------------------------------------------------------------------
INTERNAL_AUTH_TOKEN: str = agent_internal_token()

# ---------------------------------------------------------------------------
# LLM — Gemini 3 defaults per Google guidance (temperature 1.0)
# ---------------------------------------------------------------------------
# LiteLLM model strings: https://docs.litellm.ai/docs/providers
AGENT_MODEL: str = os.getenv("AGENT_MODEL", "gemini/gemini-3-flash-preview")

AGENT_FALLBACK_CHAIN: list[str] = [
    m.strip()
    for m in os.getenv(
        "AGENT_FALLBACK_CHAIN",
        "gemini/gemini-3-flash-preview,anthropic/claude-sonnet-4-6,openai/gpt-5-mini-2025-08-07",
    ).split(",")
    if m.strip()
]

# Reserved for Plan 3 (input screening)
SCREENING_MODEL: str = os.getenv(
    "SCREENING_MODEL", "gemini/gemini-3.1-flash-lite-preview"
)

AGENT_TEMPERATURE: float = float(os.getenv("AGENT_TEMPERATURE", "1.0"))
MAX_OUTPUT_TOKENS: int = int(os.getenv("MAX_OUTPUT_TOKENS", "24576"))
MAX_TOOL_CALLS_PER_TURN: int = int(os.getenv("MAX_TOOL_CALLS_PER_TURN", "10"))
MAX_CONVERSATION_TURNS: int = int(os.getenv("MAX_CONVERSATION_TURNS", "40"))

# Gemini 3 thinking depth: high | medium | low | minimal (passed via extra_body when enabled).
AGENT_THINKING_LEVEL: str = os.getenv("AGENT_THINKING_LEVEL", "high").strip().lower()
# Off by default — some LiteLLM + Google stacks reject unknown generationConfig keys.
AGENT_GEMINI_EXTRA_THINKING: bool = os.getenv(
    "AGENT_GEMINI_EXTRA_THINKING", "false"
).strip().lower() in ("1", "true", "yes")

# Request timeout for each LLM call (seconds)
LLM_REQUEST_TIMEOUT: float = float(os.getenv("LLM_REQUEST_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# Agent LLM keys — **only** *_FOR_SINGLE_AGENT (no fallback to classifier keys)
# so provider dashboards bill/trace the agent separately from classification.
# ---------------------------------------------------------------------------
AGENT_OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY_FOR_SINGLE_AGENT", "").strip()
AGENT_ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY_FOR_SINGLE_AGENT", "").strip()
AGENT_GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY_FOR_SINGLE_AGENT", "").strip()
