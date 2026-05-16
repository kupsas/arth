"""Sanity checks for demo-only helpers (no full app import — ``api.main`` reads env at import time)."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi import HTTPException

from api.auth import create_session_token
from api.constants import DEFAULT_LOCAL_USER
from api.demo import DEMO_USER_ID, DemoSessionASGIMiddleware
from api.routes import demo as demo_routes
from api.routes.chat_ws import _user_from_token


def test_demo_middleware_fly_replay_when_wrong_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP request pinned to another Machine must short-circuit with ``fly-replay`` (no app run)."""
    monkeypatch.setenv("ARTH_DEMO_MODE", "1")
    monkeypatch.setenv("FLY_MACHINE_ID", "machine-bb")

    async def _run() -> None:

        called = False

        async def inner(scope, receive, send):
            nonlocal called
            called = True
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"inner", "more_body": False})

        mw = DemoSessionASGIMiddleware(inner)
        sid = "550e8400-e29b-41d4-a716-446655440000"
        cookie = (
            f"demo_session_id={sid}; arth_demo_fly_instance=machine-aa"
        ).encode("ascii")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/health",
            "raw_path": b"/api/health",
            "query_string": b"",
            "headers": [(b"host", b"test"), (b"cookie", cookie)],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        }
        messages: list[dict] = []

        async def send_capture(msg):
            messages.append(msg)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await mw(scope, receive, send_capture)
        assert called is False
        assert messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 307
        hdrs = {k.decode().lower(): v.decode() for k, v in messages[0]["headers"]}
        assert hdrs.get("fly-replay") == "instance=machine-aa"

    asyncio.run(_run())


def test_demo_middleware_no_fly_replay_without_fly_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Local dev: no ``FLY_MACHINE_ID`` — pin cookie mismatch must not emit ``fly-replay``."""
    monkeypatch.setenv("ARTH_DEMO_MODE", "1")
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    seed = tmp_path / "seed.db"
    sqlite3.connect(seed).close()
    monkeypatch.setenv("ARTH_DEMO_SEED_PATH", str(seed))
    monkeypatch.setenv("ARTH_DEMO_SESSION_DIR", str(tmp_path / "sessions"))

    async def _run() -> None:

        called = False

        async def inner(scope, receive, send):
            nonlocal called
            called = True
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        mw = DemoSessionASGIMiddleware(inner)
        sid = "550e8400-e29b-41d4-a716-446655440000"
        cookie = (
            f"demo_session_id={sid}; arth_demo_fly_instance=machine-aa"
        ).encode("ascii")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/health",
            "raw_path": b"/api/health",
            "query_string": b"",
            "headers": [(b"host", b"test"), (b"cookie", cookie)],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        }
        messages: list[dict] = []

        async def send_capture(msg):
            messages.append(msg)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await mw(scope, receive, send_capture)
        assert called is True
        assert messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 200

    asyncio.run(_run())


def test_demo_engine_lru_evicts_least_recently_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Cap ``ARTH_DEMO_ENGINE_CACHE_MAX`` → oldest path is disposed when a new engine is added."""
    monkeypatch.setenv("ARTH_DEMO_ENGINE_CACHE_MAX", "2")
    from api.demo import DemoSessionManager

    paths: list[str] = []
    try:
        with DemoSessionManager._engines_lock:
            DemoSessionManager._engines.clear()

        for name in ("a.db", "b.db", "c.db"):
            p = tmp_path / name
            sqlite3.connect(p).close()
            paths.append(str(p.resolve()))

        s1, s2, s3 = paths
        e1 = DemoSessionManager.engine_for_path(s1)
        DemoSessionManager.engine_for_path(s2)
        DemoSessionManager.engine_for_path(s3)
        e1_again = DemoSessionManager.engine_for_path(s1)
        assert e1_again is not e1
    finally:
        for s in paths:
            DemoSessionManager.dispose_engine(s)


def test_require_demo_raises_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARTH_DEMO_MODE", raising=False)
    with pytest.raises(HTTPException) as exc:
        demo_routes._require_demo()
    assert exc.value.status_code == 404


def test_require_demo_ok_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTH_DEMO_MODE", "1")
    demo_routes._require_demo()  # no exception


def test_demo_browser_session_from_websocket_query_ok() -> None:
    from api.demo import ARTH_DEMO_SID_QUERY, demo_browser_session_from_websocket_query

    sid = "550e8400-e29b-41d4-a716-446655440000"
    scope = {
        "type": "websocket",
        "query_string": f"ticket=x&{ARTH_DEMO_SID_QUERY}={sid}".encode("ascii"),
    }
    assert demo_browser_session_from_websocket_query(scope) == sid


def test_demo_browser_session_from_websocket_query_rejects_non_uuid() -> None:
    from api.demo import demo_browser_session_from_websocket_query

    scope = {
        "type": "websocket",
        "query_string": b"arth_demo_sid=not-a-uuid",
    }
    assert demo_browser_session_from_websocket_query(scope) is None


def test_demo_browser_session_from_websocket_query_http_scope() -> None:
    from api.demo import demo_browser_session_from_websocket_query

    assert demo_browser_session_from_websocket_query({"type": "http"}) is None


# ---------------------------------------------------------------------------
# Regression: _user_from_token must return DEMO_USER_ID in demo mode.
#
# Without this, WebSocket-created sessions get user_id="local" while REST
# endpoints query with user_id="demo" → 404 → infinite reconnect loop.
# This bug has recurred twice; these tests pin the contract.
# ---------------------------------------------------------------------------


def test_user_from_token_returns_demo_user_in_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """WS identity MUST be 'demo' when ARTH_DEMO_MODE is on."""
    monkeypatch.setenv("ARTH_DEMO_MODE", "1")
    token = create_session_token("demo")
    result = _user_from_token(token)
    assert result == DEMO_USER_ID, (
        f"_user_from_token returned {result!r} in demo mode — "
        f"must return {DEMO_USER_ID!r} to match get_current_user() on REST endpoints"
    )


def test_user_from_token_returns_local_user_outside_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    """WS identity should be the default local user when demo mode is off."""
    monkeypatch.delenv("ARTH_DEMO_MODE", raising=False)
    token = create_session_token("local")
    result = _user_from_token(token)
    assert result == DEFAULT_LOCAL_USER


def test_user_from_token_returns_none_for_empty_token() -> None:
    assert _user_from_token(None) is None
    assert _user_from_token("") is None


def test_user_from_token_returns_none_for_invalid_token() -> None:
    assert _user_from_token("garbage.not.a.real.token") is None
