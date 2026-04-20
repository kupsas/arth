"""
Interactive CLI for the Arth agent (read-only Q&A over local API + SQLite).

Run from the repository root::

    python -m agent.cli
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import TextIO

from agent import config as cfg
from agent.client import create_agent_http_client
from agent.core import CONVERSATION_LIMIT_REPLY, run_agent_turn
from agent.events import (
    AgentEvent,
    ErrorEvent,
    LlmStepEvent,
    ResponseEvent,
    ScreeningBlockedEvent,
    ToolCallCompleted,
    ToolCallStarted,
)
from agent.memory import ConversationMemory
from agent.profile import generate_user_profile
from agent.run_logger import AgentRunLogger
from agent.security import CostTracker, SessionRateLimiter, screen_message

logger = logging.getLogger(__name__)


def _quiet_cli_background_loggers() -> None:
    """
    The CLI imports the full FastAPI app to talk to it in-process (ASGI transport).
    That import chain loads PDF helpers (pikepdf) and can run the IMF CPI sync
    (sdmx + ``api.services.inflation_service``), which default to noisy INFO/WARNING
    lines on stderr. Downgrade only these loggers here so the REPL stays readable;
    running ``uvicorn api.main:app`` is unaffected because it never imports this module
    as the entrypoint.
    """
    # (logger_name, level) — use ERROR for sdmx to hide both HTTP INFO and XML warnings.
    quiet: tuple[tuple[str, int], ...] = (
        ("pikepdf", logging.WARNING),
        ("sdmx", logging.ERROR),
        ("api.services.inflation_service", logging.WARNING),
        ("httpx", logging.WARNING),
        ("httpcore", logging.WARNING),
        ("urllib3", logging.WARNING),
        ("openai", logging.WARNING),
        ("anthropic", logging.WARNING),
        ("LiteLLM", logging.WARNING),
        ("litellm", logging.WARNING),
        ("google_genai", logging.WARNING),
        ("google", logging.WARNING),
    )
    for name, level in quiet:
        logging.getLogger(name).setLevel(level)


_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 20] + "\n… (truncated)"


def _print_event(ev: AgentEvent, *, debug: bool, out: TextIO) -> None:
    if isinstance(ev, LlmStepEvent):
        out.write(
            f"{_CYAN}[llm step {ev.step} | {ev.model or '?'} | finish={ev.finish_reason!r}]{_RESET}\n"
        )
        if ev.reasoning:
            out.write(f"{_YELLOW}thinking:{_RESET} {_truncate(ev.reasoning, 6000)}\n")
        # Intentionally omit streaming / pre-tool assistant text here: it duplicates the
        # final ResponseEvent ("Arth:") reply and clutters the REPL. Full step text is
        # still captured in the session log via AgentRunLogger.
        if ev.tool_intents:
            out.write(f"{_DIM}tools requested:{_RESET}\n")
            for ti in ev.tool_intents:
                name = ti.get("name", "?")
                args = ti.get("arguments", {})
                if debug:
                    out.write(f"  • {name} {args!r}\n")
                else:
                    out.write(f"  • {name} {_truncate(json.dumps(args, ensure_ascii=False), 800)}\n")
        out.write("\n")
    elif isinstance(ev, ToolCallStarted):
        if debug:
            out.write(f"{_DIM}[tool → {ev.tool_name} {ev.arguments!r}]{_RESET}\n")
        else:
            out.write(
                f"{_DIM}[tool → {ev.tool_name}]{_RESET} "
                f"{_truncate(json.dumps(ev.arguments, ensure_ascii=False), 1200)}\n"
            )
    elif isinstance(ev, ToolCallCompleted):
        # Keep a one-line receipt on the terminal; full JSON stays in the session log
        # (see AgentRunLogger.log_tool_result) so the REPL stays readable.
        status = ev.result.get("status", "?")
        out.write(
            f"{_DIM}[tool ← {ev.tool_name} | {status} | {ev.duration_ms} ms]"
            f" (full result in session log){_RESET}\n"
        )
    elif isinstance(ev, ResponseEvent):
        out.write(f"\n{_GREEN}Arth:{_RESET} {ev.content}\n\n")
    elif isinstance(ev, ScreeningBlockedEvent):
        out.write(
            f"{_YELLOW}[screening blocked: {ev.category} via {ev.layer} | {ev.latency_ms} ms]{_RESET}\n"
            f"{_GREEN}Arth:{_RESET} {ev.message}\n\n"
        )
    elif isinstance(ev, ErrorEvent):
        out.write(f"{_RED}{ev.message}{_RESET}\n")


async def async_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    _quiet_cli_background_loggers()
    print("Arth Agent — type `quit` to exit, `debug` for longer inline JSON.\n")

    run_log = AgentRunLogger.open_new_session()
    try:
        log_rel = run_log.path.resolve().relative_to(Path.cwd())
    except ValueError:
        log_rel = run_log.path.resolve()
    print(
        f"{_DIM}Session log:{_RESET} {log_rel}\n"
        f"{_DIM}(session log: LLM steps, tool args + full results, final system prompt once, reply){_RESET}\n"
    )

    client = create_agent_http_client()
    memory = ConversationMemory(max_turns=cfg.MAX_CONVERSATION_TURNS)
    debug = False
    cost_tracker = CostTracker(run_logger=run_log)
    rate_limiter = SessionRateLimiter(cfg.RATE_LIMIT_PER_MINUTE)

    try:
        print("Loading your financial profile…", end="", flush=True)
        profile = await generate_user_profile(client)
        print(" done.\n")
    except Exception:
        logger.exception("Profile load failed — continuing with empty profile")
        profile = "(Profile unavailable — tools may still work.)"

    try:
        while True:
            try:
                line = await asyncio.to_thread(input, "You: ")
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                return
            cmd = line.strip()
            if not cmd:
                continue
            if cmd.lower() in ("quit", "exit", ":q"):
                print("Bye!")
                return
            if cmd.lower() == "debug":
                debug = not debug
                print(f"Debug mode: {'on' if debug else 'off'}\n")
                continue

            def _cb(ev: AgentEvent) -> None:
                _print_event(ev, debug=debug, out=sys.stdout)

            if not rate_limiter.check_and_record():
                msg = (
                    "You're sending messages too quickly. "
                    "Take a breath and try again in a moment."
                )
                run_log.log_note("RATE_LIMIT_PER_MINUTE exceeded — message skipped")
                _cb(ResponseEvent(content=msg))
                continue

            # Hard cap before screening so we do not spend classifier tokens on a message
            # the agent will reject anyway (matches plan flow: convo check → agent).
            if memory.turn_count() >= cfg.MAX_CONVERSATION_TURNS:
                run_log.log_user_message(cmd)
                run_log.log_note(
                    "MAX_CONVERSATION_TURNS reached — screening skipped; "
                    "user message not stored in memory"
                )
                _cb(ResponseEvent(content=CONVERSATION_LIMIT_REPLY))
                continue

            sr = await screen_message(cmd, cost_tracker=cost_tracker)
            run_log.log_screening_result(
                allowed=sr.allowed,
                category=sr.category,
                layer=sr.layer,
                latency_ms=sr.latency_ms,
                rejection_message=sr.rejection_message,
            )
            if not sr.allowed:
                layer = sr.layer or "unknown"
                _cb(
                    ScreeningBlockedEvent(
                        category=sr.category or "unknown",
                        message=sr.rejection_message or "",
                        layer=layer,
                        latency_ms=sr.latency_ms,
                    )
                )
                continue

            try:
                await run_agent_turn(
                    user_message=cmd,
                    memory=memory,
                    client=client,
                    user_profile=profile,
                    event_callback=_cb,
                    run_logger=run_log,
                    cost_tracker=cost_tracker,
                )
                if debug:
                    print(
                        f"{_DIM}session LLM est. spend: ${cost_tracker.session_total_usd:.6f} | "
                        f"daily (UTC): ${cost_tracker.daily_total_usd:.6f}{_RESET}\n"
                    )
            except Exception as e:
                logger.exception("Agent turn failed")
                print(f"{_RED}Error: {e}{_RESET}\n")
    finally:
        await client.aclose()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
