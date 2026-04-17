"""
In-session conversation memory (OpenAI-style message list).

Persisted only in RAM for Sub-Plan 1; SQLite persistence comes in Plan 5.
"""

from __future__ import annotations

import copy
from typing import Any


class ConversationMemory:
    """Stores ``role`` / ``content`` / ``tool_calls`` / ``tool_call_id`` messages."""

    def __init__(self, max_turns: int = 40) -> None:
        # A "turn" here means one user message plus subsequent assistant/tool messages.
        self._max_turns = max_turns
        self._messages: list[dict[str, Any]] = []

    def add_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant_message(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls is not None:
            msg["tool_calls"] = tool_calls
        self._messages.append(msg)
        self._trim()

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
        )
        self._trim()

    def extend_messages(self, messages: list[dict[str, Any]]) -> None:
        """Append several messages at once (used at end of a ReAct turn)."""
        self._messages.extend(copy.deepcopy(messages))
        self._trim()

    def get_messages(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def _trim(self) -> None:
        """Approximate cap: drop oldest user turns when over limit."""
        if len(self._messages) <= self._max_turns * 6:
            return
        # Drop messages from the start until we're under the cap.
        while len(self._messages) > self._max_turns * 4:
            self._messages.pop(0)

    def turn_count(self) -> int:
        return sum(1 for m in self._messages if m.get("role") == "user")
