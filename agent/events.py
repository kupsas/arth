"""
Structured events emitted by the ReAct loop.

Same event objects can be printed in the CLI (now) or streamed over WebSocket
(Sub-Plan 5) without changing core agent logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ThinkingEvent:
    """Optional reasoning / status text for the UI."""

    content: str


@dataclass
class ThinkingDoneEvent:
    """Tells the UI to collapse the live thinking block (reasoning phase ended)."""

    pass


@dataclass
class LlmStepEvent:
    """
    One model forward pass inside the ReAct loop (before tools run, if any).

    Emitted after each ``chat_completion`` returns so the CLI / logs can show
    what the model ``finish_reason`` was, any visible ``content``, best-effort
    ``reasoning``, and which tools it asked to call.
    """

    step: int
    model: str | None
    finish_reason: str | None
    content: str | None
    reasoning: str | None
    tool_intents: list[dict[str, Any]]


@dataclass
class ToolCallStarted:
    tool_name: str
    arguments: dict[str, Any]
    tool_call_id: str | None = None


@dataclass
class ToolCallCompleted:
    tool_name: str
    result: dict[str, Any]
    duration_ms: int
    tool_call_id: str | None = None


@dataclass
class TokenEvent:
    """Reserved for streaming token chunks (Plan 5)."""

    token: str


@dataclass
class ResponseEvent:
    """Final natural-language reply for this user turn."""

    content: str


@dataclass
class ErrorEvent:
    message: str
    recoverable: bool = True


@dataclass
class ScreeningBlockedEvent:
    """Emitted when Layer-1/Layer-2 screening blocks a user message before the agent runs."""

    category: str
    message: str
    layer: str
    latency_ms: int


# Union type for type hints on callbacks
AgentEvent = (
    ThinkingEvent
    | ThinkingDoneEvent
    | LlmStepEvent
    | ToolCallStarted
    | ToolCallCompleted
    | TokenEvent
    | ResponseEvent
    | ErrorEvent
    | ScreeningBlockedEvent
)
