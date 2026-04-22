"""
Per-user classifier API keys + onboarding thresholds (Track 2 Phase 3c).

``UserSecrets.secrets_json`` stores env-style keys (encrypted). During email
pipeline runs we temporarily overlay :mod:`pipeline.config` so
:func:`pipeline.llm_classifier.classify_llm` sees the logged-in user's keys
without touching process-wide environment variables permanently.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, select

from api.models import UserSecrets
from pipeline import config as pc


def _triplet_from_process_env() -> tuple[str, str, str]:
    """Mirror ``pipeline.config`` env resolution (keys may be empty strings)."""
    o = (
        os.getenv("OPENAI_API_KEY_FOR_CLASSIFIER", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    a = (
        os.getenv("ANTHROPIC_API_KEY_FOR_CLASSIFIER", "").strip()
        or os.getenv("ANTHROPIC_API_KEY", "").strip()
    )
    g = (
        os.getenv("GOOGLE_API_KEY_FOR_CLASSIFIER", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )
    return o, a, g


def _triplet_from_secrets_dict(data: dict[str, str]) -> tuple[str | None, str | None, str | None]:
    """Return optional overrides from decrypted ``UserSecrets`` JSON."""
    o = (
        str(data.get("OPENAI_API_KEY_FOR_CLASSIFIER") or "").strip()
        or str(data.get("OPENAI_API_KEY") or "").strip()
        or None
    )
    a = (
        str(data.get("ANTHROPIC_API_KEY_FOR_CLASSIFIER") or "").strip()
        or str(data.get("ANTHROPIC_API_KEY") or "").strip()
        or None
    )
    g = (
        str(data.get("GOOGLE_API_KEY_FOR_CLASSIFIER") or "").strip()
        or str(data.get("GOOGLE_API_KEY") or "").strip()
        or None
    )
    return o, a, g


def user_has_classifier_api_key(session: Session, user_id: str) -> bool:
    """True if env **or** stored user secrets provide at least one LLM provider key."""
    if any(_triplet_from_process_env()):
        return True
    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
    if not row or not row.secrets_json:
        return False
    try:
        raw = json.loads(row.secrets_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(raw, dict):
        return False
    o, a, g = _triplet_from_secrets_dict({str(k): str(v) for k, v in raw.items()})
    return bool((o or "").strip() or (a or "").strip() or (g or "").strip())


def effective_onboarding_unknown_threshold(session: Session, user_id: str) -> int:
    """Lower pause threshold when LLM cannot trim unknowns (no keys or ``LLM_MODEL=none``)."""
    low = int(os.getenv("ONBOARDING_UNKNOWN_THRESHOLD_LOW", "10"))
    high = int(os.getenv("ONBOARDING_UNKNOWN_THRESHOLD", "20"))
    if str(pc.LLM_MODEL or "").strip().lower() == "none":
        return low
    if not user_has_classifier_api_key(session, user_id):
        return low
    return high


@contextmanager
def user_classifier_runtime(session: Session, user_id: str) -> Iterator[None]:
    """Temporarily overlay ``pipeline.config`` LLM keys from ``UserSecrets``."""
    snap_o, snap_a, snap_g = pc.OPENAI_API_KEY, pc.ANTHROPIC_API_KEY, pc.GOOGLE_API_KEY
    try:
        row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
        if row and row.secrets_json:
            try:
                raw = json.loads(row.secrets_json)
            except json.JSONDecodeError:
                raw = None
            if isinstance(raw, dict):
                o, a, g = _triplet_from_secrets_dict({str(k): str(v) for k, v in raw.items()})
                if o:
                    pc.OPENAI_API_KEY = o
                if a:
                    pc.ANTHROPIC_API_KEY = a
                if g:
                    pc.GOOGLE_API_KEY = g
        yield
    finally:
        pc.OPENAI_API_KEY, pc.ANTHROPIC_API_KEY, pc.GOOGLE_API_KEY = snap_o, snap_a, snap_g
