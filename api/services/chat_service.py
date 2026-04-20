"""
Persisted Arth dashboard chat sessions and OpenAI-format message history.

Messages mirror :class:`agent.memory.ConversationMemory` contents (no system prompt —
that is rebuilt each turn by the agent).
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from sqlmodel import Session, col, select

from api.models import ChatMessage, ChatSession

TITLE_MAX_LEN = 60


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _truncate_title(text: str, max_len: int = TITLE_MAX_LEN) -> str:
    t = text.strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def create_session(session: Session, user_id: str) -> ChatSession:
    """Create an empty chat session row."""
    sid = str(uuid.uuid4())
    row = ChatSession(id=sid, user_id=user_id, title=None, created_at=_utc_now(), updated_at=_utc_now())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_session(session: Session, session_id: str, user_id: str) -> ChatSession | None:
    """Return the session if it exists, belongs to the user, and is not archived."""
    row = session.get(ChatSession, session_id)
    if row is None or row.user_id != user_id or row.is_archived:
        return None
    return row


def list_sessions(
    session: Session,
    user_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[ChatSession]:
    """Newest first (by last activity)."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .where(ChatSession.is_archived == 0)
        .order_by(col(ChatSession.updated_at).desc())
        .offset(max(0, offset))
        .limit(min(200, max(1, limit)))
    )
    return list(session.exec(stmt).all())


def update_session_title(session: Session, session_id: str, user_id: str, title: str) -> ChatSession | None:
    row = get_session(session, session_id, user_id)
    if row is None:
        return None
    row.title = title.strip() or None
    row.updated_at = _utc_now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def archive_session(session: Session, session_id: str, user_id: str) -> bool:
    row = get_session(session, session_id, user_id)
    if row is None:
        return False
    row.is_archived = 1
    row.updated_at = _utc_now()
    session.add(row)
    session.commit()
    return True


def _row_from_openai_dict(session_id: str, m: dict[str, Any]) -> ChatMessage:
    role = str(m.get("role") or "")
    content = m.get("content")
    if content is not None and not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)

    tool_calls = m.get("tool_calls")
    tool_calls_json: str | None = None
    if tool_calls is not None:
        tool_calls_json = json.dumps(tool_calls, ensure_ascii=False, default=str)

    tool_call_id = m.get("tool_call_id")
    if tool_call_id is not None:
        tool_call_id = str(tool_call_id)

    tool_name = m.get("name")  # rarely present; OpenAI tool role uses tool_call_id only

    # Arth-only: model reasoning + chronological activity (not sent to LLM on replay).
    meta: dict[str, Any] = {}
    arth = m.get("_arth_thinking")
    if isinstance(arth, str) and arth.strip():
        meta["_arth_thinking"] = arth
    tl = m.get("_arth_timeline")
    if isinstance(tl, list) and tl:
        meta["_arth_timeline"] = tl

    metadata_json: str | None = json.dumps(meta, ensure_ascii=False) if meta else None

    return ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        tool_calls_json=tool_calls_json,
        tool_call_id=tool_call_id,
        tool_name=str(tool_name) if tool_name else None,
        metadata_json=metadata_json,
        created_at=_utc_now(),
    )


def _openai_dict_from_row(row: ChatMessage) -> dict[str, Any]:
    m: dict[str, Any] = {"role": row.role}
    if row.content is not None:
        m["content"] = row.content
    if row.tool_calls_json:
        try:
            m["tool_calls"] = json.loads(row.tool_calls_json)
        except json.JSONDecodeError:
            m["tool_calls"] = []
    if row.role == "tool":
        if row.tool_call_id:
            m["tool_call_id"] = row.tool_call_id
        # OpenAI format requires content for tool messages
        if "content" not in m:
            m["content"] = ""
    if row.metadata_json:
        try:
            meta = json.loads(row.metadata_json)
            if isinstance(meta, dict):
                if isinstance(meta.get("_arth_thinking"), str):
                    m["_arth_thinking"] = meta["_arth_thinking"]
                if isinstance(meta.get("_arth_timeline"), list):
                    m["_arth_timeline"] = meta["_arth_timeline"]
        except json.JSONDecodeError:
            pass
    return m


def load_messages(session: Session, session_id: str) -> list[dict[str, Any]]:
    """Load conversation history in OpenAI chat format, in order."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(col(ChatMessage.id).asc())
    )
    rows = list(session.exec(stmt).all())
    return [_openai_dict_from_row(r) for r in rows]


def replace_session_messages(
    session: Session,
    session_id: str,
    user_id: str,
    messages: list[dict[str, Any]],
) -> ChatSession | None:
    """
    Replace all stored messages for a session with the given OpenAI-format list.

    Updates ``updated_at`` and sets ``title`` from the first user message when still empty.
    """
    cs = get_session(session, session_id, user_id)
    if cs is None:
        return None

    existing = session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all()
    for msg in existing:
        session.delete(msg)

    for m in messages:
        session.add(_row_from_openai_dict(session_id, m))

    cs.updated_at = _utc_now()
    if not cs.title:
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                cs.title = _truncate_title(str(m["content"]))
                break

    session.add(cs)
    session.commit()
    session.refresh(cs)
    return cs
