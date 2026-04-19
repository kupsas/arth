"""
Estimate LLM spend from LiteLLM usage objects — session + rolling UTC-day totals.

Used for session log lines and a soft daily alert (no hard block in v1).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agent import config as cfg
from agent.run_logger import AgentRunLogger

logger = logging.getLogger(__name__)


def _usage_tokens(response: Any) -> tuple[int, int, int]:
    """Return (prompt_tokens, completion_tokens, total_tokens) best-effort."""
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


def _response_model_id(response: Any) -> str | None:
    return getattr(response, "model", None) or getattr(response, "model_id", None)


def estimate_cost_usd(*, model: str | None, prompt_tokens: int, completion_tokens: int) -> float:
    """USD estimate from config pricing table; 0.0 if model unknown."""
    if not model:
        return 0.0
    pricing = cfg.AGENT_MODEL_PRICING.get(model.strip())
    if not pricing:
        return 0.0
    inp = float(pricing.get("input", 0.0))
    out = float(pricing.get("output", 0.0))
    return (prompt_tokens / 1_000_000.0) * inp + (completion_tokens / 1_000_000.0) * out


class CostTracker:
    """
    Accumulate estimated spend for the current process.

    ``daily`` resets when the UTC calendar day changes (simple in-memory counter).
    """

    def __init__(self, *, run_logger: AgentRunLogger | None = None) -> None:
        self._run_logger = run_logger
        self.session_total_usd = 0.0
        self.session_total_prompt_tokens: int = 0
        self.session_total_completion_tokens: int = 0
        self.daily_total_usd = 0.0
        self._day_key: str | None = None

    def _rollover_day_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if self._day_key != today:
            self._day_key = today
            self.daily_total_usd = 0.0

    def record_litellm_response(
        self,
        *,
        response: Any,
        call_type: str,
        model: str | None = None,
    ) -> None:
        """
        Parse ``response.usage``, add to totals, optionally append to session log.

        ``call_type`` is ``\"agent\"`` or ``\"screening\"`` for log readability.

        When ``model`` is set, it overrides ``response.model`` for pricing lookup
        (LiteLLM sometimes returns a different id string than the request id).
        """
        self._rollover_day_if_needed()
        requested = model.strip() if isinstance(model, str) and model.strip() else None
        model_id = requested or _response_model_id(response)
        pt, ct, tt = _usage_tokens(response)
        est = estimate_cost_usd(model=model_id, prompt_tokens=pt, completion_tokens=ct)
        prev_daily = self.daily_total_usd
        self.session_total_usd += est
        self.session_total_prompt_tokens += pt
        self.session_total_completion_tokens += ct
        self.daily_total_usd += est

        # Log once when crossing the threshold (avoid spamming every completion).
        if prev_daily < cfg.COST_ALERT_DAILY_USD <= self.daily_total_usd:
            logger.warning(
                "Estimated daily LLM spend crossed %.2f USD (now %.4f USD)",
                cfg.COST_ALERT_DAILY_USD,
                self.daily_total_usd,
            )

        if self._run_logger is not None:
            self._run_logger.log_llm_usage(
                model=model_id,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                estimated_cost_usd=est,
                session_total_usd=self.session_total_usd,
                daily_total_usd=self.daily_total_usd,
                call_type=call_type,
            )
