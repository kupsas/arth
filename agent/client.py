"""
Shared ``httpx.AsyncClient`` wired to the FastAPI app via ASGITransport.

Uses ``X-Arth-Internal`` so ``Depends(get_current_user)`` succeeds without cookies.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from httpx import ASGITransport, AsyncClient

from agent import config as cfg


def create_agent_http_client() -> AsyncClient:
    """Build a new client. **Lazy-imports** ``api.main:app`` to reduce import cycles."""
    from api.main import app  # noqa: WPS433 — intentional lazy import

    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://arth.test",
        headers={"X-Arth-Internal": cfg.INTERNAL_AUTH_TOKEN},
        timeout=120.0,
    )


@asynccontextmanager
async def agent_http_client() -> AsyncIterator[AsyncClient]:
    client = create_agent_http_client()
    try:
        yield client
    finally:
        await client.aclose()
