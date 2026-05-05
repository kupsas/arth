"""
Agent LLM keys + optional model overrides stored in ``UserSecrets.secrets_json``.

Mirrors :mod:`api.services.classifier_runtime` — overlays :mod:`agent.config` for chat turns.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, select

import agent.config as ac
from api.models import UserSecrets


def _triplet_from_process_env() -> tuple[str, str, str]:
    return (
        os.getenv("OPENAI_API_KEY_FOR_SINGLE_AGENT", "").strip(),
        os.getenv("ANTHROPIC_API_KEY_FOR_SINGLE_AGENT", "").strip(),
        os.getenv("GOOGLE_API_KEY_FOR_SINGLE_AGENT", "").strip(),
    )


def _secrets_dict(session: Session, user_id: str) -> dict[str, str]:
    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
    if not row or not row.secrets_json:
        return {}
    try:
        raw = json.loads(row.secrets_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def user_agent_api_key_presence(session: Session, user_id: str) -> tuple[bool, bool, bool]:
    """Env or stored keys for the conversational agent."""
    eo, ea, eg = _triplet_from_process_env()
    ho = bool(eo)
    ha = bool(ea)
    hg = bool(eg)
    data = _secrets_dict(session, user_id)
    if data.get("OPENAI_API_KEY_FOR_SINGLE_AGENT", "").strip():
        ho = True
    if data.get("ANTHROPIC_API_KEY_FOR_SINGLE_AGENT", "").strip():
        ha = True
    if data.get("GOOGLE_API_KEY_FOR_SINGLE_AGENT", "").strip():
        hg = True
    return ho, ha, hg


def user_stored_agent_api_key_presence(session: Session, user_id: str) -> tuple[bool, bool, bool]:
    """Stored keys only (matches dashboard save/remove)."""
    data = _secrets_dict(session, user_id)
    return (
        bool(data.get("OPENAI_API_KEY_FOR_SINGLE_AGENT", "").strip()),
        bool(data.get("ANTHROPIC_API_KEY_FOR_SINGLE_AGENT", "").strip()),
        bool(data.get("GOOGLE_API_KEY_FOR_SINGLE_AGENT", "").strip()),
    )


def user_has_any_agent_api_key(session: Session, user_id: str) -> bool:
    ho, ha, hg = user_agent_api_key_presence(session, user_id)
    return ho or ha or hg


def effective_agent_model(session: Session, user_id: str) -> str:
    data = _secrets_dict(session, user_id)
    raw = (data.get("AGENT_MODEL") or "").strip()
    return raw or ac.AGENT_MODEL


def effective_agent_fallback_chain(session: Session, user_id: str) -> list[str]:
    data = _secrets_dict(session, user_id)
    raw = (data.get("AGENT_FALLBACK_CHAIN") or "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(ac.AGENT_FALLBACK_CHAIN)


@contextmanager
def user_agent_runtime(session: Session, user_id: str) -> Iterator[None]:
    """Overlay agent LiteLLM keys + optional model strings from ``UserSecrets``."""
    snap_o = ac.AGENT_OPENAI_API_KEY
    snap_a = ac.AGENT_ANTHROPIC_API_KEY
    snap_g = ac.AGENT_GOOGLE_API_KEY
    snap_model = ac.AGENT_MODEL
    snap_fb = list(ac.AGENT_FALLBACK_CHAIN)
    try:
        data = _secrets_dict(session, user_id)
        o = data.get("OPENAI_API_KEY_FOR_SINGLE_AGENT", "").strip()
        a = data.get("ANTHROPIC_API_KEY_FOR_SINGLE_AGENT", "").strip()
        g = data.get("GOOGLE_API_KEY_FOR_SINGLE_AGENT", "").strip()
        if o:
            ac.AGENT_OPENAI_API_KEY = o
        if a:
            ac.AGENT_ANTHROPIC_API_KEY = a
        if g:
            ac.AGENT_GOOGLE_API_KEY = g
        m = (data.get("AGENT_MODEL") or "").strip()
        if m:
            ac.AGENT_MODEL = m
        fb_raw = (data.get("AGENT_FALLBACK_CHAIN") or "").strip()
        if fb_raw:
            ac.AGENT_FALLBACK_CHAIN = [x.strip() for x in fb_raw.split(",") if x.strip()]
        yield
    finally:
        ac.AGENT_OPENAI_API_KEY = snap_o
        ac.AGENT_ANTHROPIC_API_KEY = snap_a
        ac.AGENT_GOOGLE_API_KEY = snap_g
        ac.AGENT_MODEL = snap_model
        ac.AGENT_FALLBACK_CHAIN = snap_fb
