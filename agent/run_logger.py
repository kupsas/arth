"""
Append-only session logs for the conversational agent (CLI and future surfaces).

Logs live under ``agent/logs/`` — one file per CLI process so you can scroll back
through a whole REPL session.  Not committed to git (see ``.gitignore``).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path(__file__).resolve().parent / "logs"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgentRunLogger:
    """
    Write a human-readable trace of one agent session to disk.

    Call :meth:`open_new_session` at CLI startup; pass the instance into each
    :func:`agent.core.run_agent_turn`.
    """

    def __init__(self, path: Path, *, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        # The rendered system prompt is identical across turns for a given CLI session;
        # log it once so the file stays useful without huge duplication.
        self._final_system_prompt_logged = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write(
            f"# Arth agent run log\n"
            f"# started={_utc_stamp()}\n"
            f"# file={path.name}\n"
            f"# session_id={session_id}\n\n"
        )

    @classmethod
    def open_new_session(cls) -> AgentRunLogger:
        """Create ``agent/logs/session-YYYYMMDD-HHMMSS-<8hex>.log``."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        short = uuid.uuid4().hex[:8]
        path = _LOG_DIR / f"session-{ts}-{short}.log"
        return cls(path, session_id=f"{ts}-{short}")

    def _write(self, text: str) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def log_user_message(self, text: str) -> None:
        self._write(f"\n{'=' * 72}\n[{_utc_stamp()}] USER\n{'=' * 72}\n{text}\n")

    def log_final_system_prompt_once(self, text: str) -> None:
        """
        Record the exact system string sent to the model (placeholders already filled).

        This is the post-:func:`agent.prompts.load_system_prompt` string, not the raw
        YAML template. Written at most once per log file.
        """
        if self._final_system_prompt_logged:
            return
        self._final_system_prompt_logged = True
        self._write(
            f"\n{'=' * 72}\n"
            f"[{_utc_stamp()}] SYSTEM PROMPT (final, rendered — same for rest of session)\n"
            f"{'=' * 72}\n"
            f"{text.rstrip()}\n"
        )

    def log_llm_step(
        self,
        *,
        step: int,
        model: str | None,
        finish_reason: str | None,
        content: str | None,
        reasoning: str | None,
        tool_intents: list[dict[str, Any]],
    ) -> None:
        self._write(
            f"\n{'─' * 72}\n"
            f"[{_utc_stamp()}] LLM step {step}"
            f" | model={model!r} | finish_reason={finish_reason!r}\n"
            f"{'─' * 72}\n"
        )
        if reasoning:
            self._write("--- reasoning / thinking (best-effort) ---\n")
            self._write(reasoning.strip() + "\n")
        if content and str(content).strip():
            self._write("--- assistant message (before tools, if any) ---\n")
            self._write(str(content).strip() + "\n")
        if tool_intents:
            self._write("--- tool calls requested ---\n")
            self._write(json.dumps(tool_intents, ensure_ascii=False, indent=2) + "\n")

    def log_tool_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        duration_ms: int,
    ) -> None:
        self._write(
            f"\n[{_utc_stamp()}] TOOL {tool_name} ({duration_ms} ms)\n"
            f"arguments:\n{json.dumps(arguments, ensure_ascii=False, indent=2)}\n"
            f"result:\n{json.dumps(result, ensure_ascii=False, indent=2, default=str)}\n"
        )

    def log_final_assistant(self, text: str) -> None:
        self._write(
            f"\n{'=' * 72}\n[{_utc_stamp()}] ASSISTANT (final)\n{'=' * 72}\n{text}\n"
        )

    def log_note(self, message: str) -> None:
        self._write(f"\n[{_utc_stamp()}] NOTE: {message}\n")
