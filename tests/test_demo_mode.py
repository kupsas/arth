"""Sanity checks for demo-only helpers (no full app import — ``api.main`` reads env at import time)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.auth import create_session_token
from api.constants import DEFAULT_LOCAL_USER
from api.demo import DEMO_USER_ID
from api.routes import demo as demo_routes
from api.routes.chat_ws import _user_from_token


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
