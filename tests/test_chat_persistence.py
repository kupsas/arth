"""
Chat session + message persistence (Sub-Plan 5 — dashboard agent).
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import api.models  # noqa: F401 — register ChatSession / ChatMessage with SQLModel metadata
from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.services import chat_service
from fastapi.testclient import TestClient


@pytest.fixture(name="engine")
def in_memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(name="db")
def db_session(engine):
    with Session(engine) as session:
        yield session


def test_create_session_and_replace_messages_roundtrip(db: Session) -> None:
    row = chat_service.create_session(db, "sashank")
    chat_service.replace_session_messages(
        db,
        row.id,
        "sashank",
        [
            {"role": "user", "content": "What is 2+2?"},
        ],
    )
    messages = chat_service.load_messages(db, row.id)
    assert len(messages) == 1
    assert messages[0] == {"role": "user", "content": "What is 2+2?"}


def test_assistant_arth_timeline_roundtrip(db: Session) -> None:
    """Chronological activity segments persist in metadata_json."""
    row = chat_service.create_session(db, "sashank")
    timeline = [
        {"kind": "thinking", "content": "Plan step."},
        {
            "kind": "tools",
            "tools": [
                {
                    "name": "demo_tool",
                    "arguments": {"x": 1},
                    "result": {"ok": True},
                    "duration_ms": 12,
                }
            ],
        },
        {"kind": "thinking", "content": "Second thought."},
    ]
    chat_service.replace_session_messages(
        db,
        row.id,
        "sashank",
        [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "Done.",
                "_arth_timeline": timeline,
            },
        ],
    )
    messages = chat_service.load_messages(db, row.id)
    assert len(messages) == 2
    assert messages[1].get("_arth_timeline") == timeline


def test_assistant_arth_thinking_roundtrip(db: Session) -> None:
    """Persisted model thinking is stored in metadata_json and rehydrated on load."""
    row = chat_service.create_session(db, "sashank")
    chat_service.replace_session_messages(
        db,
        row.id,
        "sashank",
        [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "Hello.",
                "_arth_thinking": "Step 1: greet the user.",
            },
        ],
    )
    messages = chat_service.load_messages(db, row.id)
    assert len(messages) == 2
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Hello."
    assert messages[1].get("_arth_thinking") == "Step 1: greet the user."


@pytest.fixture(name="client")
def api_client(engine):
    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "sashank"

    import api.database as _db_mod

    _original_init = _db_mod.init_db
    _db_mod.init_db = lambda: None

    with TestClient(app) as c:
        yield c

    _db_mod.init_db = _original_init
    app.dependency_overrides.clear()


def test_list_chat_sessions_http(engine, client: TestClient) -> None:
    r = client.get("/api/chat/sessions")
    assert r.status_code == 200
    assert r.json() == []

    with Session(engine) as db:
        chat_service.create_session(db, "sashank")

    r2 = client.get("/api/chat/sessions")
    assert r2.status_code == 200
    body = r2.json()
    assert len(body) == 1
    assert body[0]["message_count"] == 0
    assert body[0]["title"] is None
