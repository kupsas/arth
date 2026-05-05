"""
Dashboard agent chat — REST session CRUD + WebSocket streaming of agent events.

Local installs do not require a session cookie; WebSocket accepts missing credentials.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, assert_never
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from agent import config as cfg
from agent.client import agent_http_client
from agent.core import CONVERSATION_LIMIT_REPLY, run_agent_turn
from agent.events import (
    AgentEvent,
    ErrorEvent,
    LlmStepEvent,
    ResponseEvent,
    ScreeningBlockedEvent,
    ThinkingDoneEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallCompleted,
    ToolCallStarted,
)
from agent.memory import ConversationMemory
from agent.profile import generate_user_profile
from agent.security import CostTracker, SessionRateLimiter, screen_message
from api.auth import COOKIE_NAME, create_session_token, get_current_user, verify_session_token
from api.constants import DEFAULT_LOCAL_USER
from api.database import SQLiteSerializingSession, get_engine, get_session
from api.services import chat_service
from api.services.agent_runtime import user_agent_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["Agent chat"])


# --- REST -----------------------------------------------------------------

class ChatSessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class ChatSessionDetail(ChatSessionSummary):
    messages: list[dict[str, Any]]


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.get("/sessions", response_model=list[ChatSessionSummary])
def list_chat_sessions(
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ChatSessionSummary]:
    rows = chat_service.list_sessions(session, user, limit=limit, offset=offset)
    return [ChatSessionSummary.model_validate(r) for r in rows]


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
def get_chat_session(
    session_id: str,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> ChatSessionDetail:
    row = chat_service.get_session(session, session_id, user)
    if row is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    msgs = chat_service.load_messages(session, session_id)
    return ChatSessionDetail(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        messages=msgs,
    )


@router.patch("/sessions/{session_id}", response_model=ChatSessionSummary)
def rename_chat_session(
    session_id: str,
    body: RenameBody,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> ChatSessionSummary:
    updated = chat_service.update_session_title(session, session_id, user, body.title)
    if updated is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return ChatSessionSummary.model_validate(updated)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_chat_session(
    session_id: str,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> None:
    ok = chat_service.archive_session(session, session_id, user)
    if not ok:
        raise HTTPException(status_code=404, detail="Chat session not found")


@router.get("/ws-ticket")
def get_ws_ticket(user: str = Depends(get_current_user)) -> dict[str, str]:
    """Return a short-lived token the browser passes as ``?ticket=`` on the
    WebSocket URL.  Needed because the WS may connect directly to FastAPI
    (bypassing the Next.js proxy), so the httpOnly session cookie is absent."""
    return {"ticket": create_session_token(user)}


# --- WebSocket ------------------------------------------------------------

def _user_from_token(token: str | None) -> str | None:
    """Verify a session token or WS ticket — identity is always the local install user."""
    if not token:
        return None
    try:
        verify_session_token(token)
        return DEFAULT_LOCAL_USER
    except HTTPException:
        return None


def _event_to_wire(ev: AgentEvent) -> dict[str, Any]:
    """Map agent dataclass events to JSON the dashboard understands."""
    if isinstance(ev, ThinkingEvent):
        return {"type": "thinking", "content": ev.content}
    if isinstance(ev, ThinkingDoneEvent):
        return {"type": "thinking_done"}
    if isinstance(ev, LlmStepEvent):
        return {
            "type": "llm_step",
            "step": ev.step,
            "model": ev.model,
            "finish_reason": ev.finish_reason,
            "content": ev.content,
            "reasoning": ev.reasoning,
            "tool_intents": ev.tool_intents,
        }
    if isinstance(ev, ToolCallStarted):
        return {
            "type": "tool_call_started",
            "tool_name": ev.tool_name,
            "arguments": ev.arguments,
            "tool_call_id": ev.tool_call_id,
        }
    if isinstance(ev, ToolCallCompleted):
        return {
            "type": "tool_call_completed",
            "tool_name": ev.tool_name,
            "result": ev.result,
            "duration_ms": ev.duration_ms,
            "tool_call_id": ev.tool_call_id,
        }
    if isinstance(ev, TokenEvent):
        return {"type": "token", "token": ev.token}
    if isinstance(ev, ResponseEvent):
        return {"type": "response", "content": ev.content}
    if isinstance(ev, ErrorEvent):
        return {"type": "error", "message": ev.message, "recoverable": ev.recoverable}
    if isinstance(ev, ScreeningBlockedEvent):
        return {
            "type": "screening_blocked",
            "category": ev.category,
            "message": ev.message,
            "layer": ev.layer,
            "latency_ms": ev.latency_ms,
        }
    assert_never(ev)


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket) -> None:
    """Bidirectional agent chat; same cookie auth as the REST API."""
    raw_q = websocket.query_params.get("session_id")
    session_id = str(raw_q).strip() if raw_q else None
    if session_id == "":
        session_id = None
    cookie = websocket.cookies.get(COOKIE_NAME)
    user = _user_from_token(cookie)
    if user is None:
        ticket = websocket.query_params.get("ticket")
        user = _user_from_token(ticket)
    if user is None:
        user = DEFAULT_LOCAL_USER

    await websocket.accept()

    memory = ConversationMemory(max_turns=cfg.MAX_CONVERSATION_TURNS)
    cost_tracker = CostTracker(run_logger=None)
    rate_limiter = SessionRateLimiter(cfg.RATE_LIMIT_PER_MINUTE)
    chat_session_id: str | None = session_id

    # Load or create persisted session row + hydrate memory.

    with SQLiteSerializingSession(get_engine()) as db:
        if chat_session_id:
            cs = chat_service.get_session(db, chat_session_id, user)
            if cs is None:
                await websocket.send_json(
                    {"type": "error", "message": "We couldn't open that chat — it may have been archived. Start a new chat?", "recoverable": False}
                )
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            hist = chat_service.load_messages(db, chat_session_id)
            if hist:
                memory.extend_messages(hist)
        else:
            cs = chat_service.create_session(db, user)
            chat_session_id = cs.id

    assert chat_session_id is not None

    with SQLiteSerializingSession(get_engine()) as db:
        cs_row = chat_service.get_session(db, chat_session_id, user)
    title = cs_row.title if cs_row else None
    await websocket.send_json(
        {"type": "session_ready", "session_id": chat_session_id, "title": title}
    )

    current_turn: asyncio.Task | None = None

    async def emit(ev: AgentEvent) -> None:
        await websocket.send_json(_event_to_wire(ev))

    async def persist_memory() -> None:
        """Snapshot OpenAI-format messages to SQLite after each successful turn."""
        with SQLiteSerializingSession(get_engine()) as db:
            chat_service.replace_session_messages(db, chat_session_id, user, memory.get_messages())

    async def run_one_turn(user_text: str) -> None:
        nonlocal memory
        if not rate_limiter.check_and_record():
            await emit(
                ResponseEvent(
                    content=(
                        "You're sending messages too quickly. "
                        "Take a breath and try again in a moment."
                    )
                )
            )
            await websocket.send_json({"type": "done"})
            return

        if memory.turn_count() >= cfg.MAX_CONVERSATION_TURNS:
            await emit(ResponseEvent(content=CONVERSATION_LIMIT_REPLY))
            await websocket.send_json({"type": "done"})
            return

        with SQLiteSerializingSession(get_engine()) as db_agent:
            with user_agent_runtime(db_agent, user):
                sr = await screen_message(user_text, cost_tracker=cost_tracker)
                if not sr.allowed:
                    await emit(
                        ScreeningBlockedEvent(
                            category=sr.category or "unknown",
                            message=sr.rejection_message or "",
                            layer=sr.layer or "unknown",
                            latency_ms=sr.latency_ms,
                        )
                    )
                    await websocket.send_json({"type": "done"})
                    return

                async with agent_http_client() as client:
                    profile = await generate_user_profile(client)
                    await run_agent_turn(
                        user_message=user_text,
                        memory=memory,
                        client=client,
                        user_profile=profile,
                        event_callback=emit,
                        run_logger=None,
                        cost_tracker=cost_tracker,
                    )

        await persist_memory()
        await websocket.send_json({"type": "done"})

    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "stop":
                if current_turn is not None and not current_turn.done():
                    current_turn.cancel()
                    try:
                        await current_turn
                    except asyncio.CancelledError:
                        pass
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Generation stopped.",
                            "recoverable": True,
                        }
                    )
                    await websocket.send_json({"type": "done"})
                current_turn = None
                continue

            if mtype != "send_message":
                continue

            content = str(msg.get("content") or "").strip()
            if not content:
                await websocket.send_json({"type": "done"})
                continue

            if current_turn is not None and not current_turn.done():
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Already processing a message — wait or press Stop.",
                        "recoverable": True,
                    }
                )
                await websocket.send_json({"type": "done"})
                continue

            async def _wrapped_turn() -> None:
                try:
                    await run_one_turn(content)
                except asyncio.CancelledError:
                    await websocket.send_json(
                        {"type": "error", "message": "Generation stopped.", "recoverable": True}
                    )
                    await websocket.send_json({"type": "done"})
                    raise
                except Exception:
                    logger.exception("Chat WebSocket turn failed")
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Something broke while answering — try again? If it keeps happening, refresh the page.",
                            "recoverable": True,
                        }
                    )
                    await websocket.send_json({"type": "done"})

            current_turn = asyncio.create_task(_wrapped_turn())

    except WebSocketDisconnect:
        logger.debug("Chat WebSocket disconnected")
    finally:
        if current_turn is not None and not current_turn.done():
            current_turn.cancel()
