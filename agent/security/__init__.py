"""Input screening, rate limits, cost tracking, and tool-output hardening (Sub-Plan 3)."""

from __future__ import annotations

__all__ = [
    "CostTracker",
    "REJECTION_MESSAGES",
    "ScreeningResult",
    "SessionRateLimiter",
    "estimate_cost_usd",
    "screen_message",
    "wrap_tool_output",
]


def __getattr__(name: str):
    """Lazy exports so ``import agent.security.cost_tracker`` does not load LiteLLM."""
    if name in ("CostTracker", "estimate_cost_usd"):
        from agent.security import cost_tracker

        return getattr(cost_tracker, name)
    if name == "wrap_tool_output":
        from agent.security.output_sanitizer import wrap_tool_output

        return wrap_tool_output
    if name == "SessionRateLimiter":
        from agent.security.rate_limiter import SessionRateLimiter

        return SessionRateLimiter
    if name in ("REJECTION_MESSAGES", "ScreeningResult", "screen_message"):
        from agent.security import screening

        return getattr(screening, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
