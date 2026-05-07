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


def user_classifier_api_key_presence(session: Session, user_id: str) -> tuple[bool, bool, bool]:
    """Return (openai, anthropic, google) if **either** process env **or** ``UserSecrets`` has that key.

    Used by :func:`user_has_classifier_api_key` for runtime / onboarding thresholds so deployments
    with global API keys still enable LLM without per-user paste.
    """
    eo, ea, eg = _triplet_from_process_env()
    ho = bool(eo)
    ha = bool(ea)
    hg = bool(eg)
    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
    if row and row.secrets_json:
        try:
            raw = json.loads(row.secrets_json)
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, dict):
            o, a, g = _triplet_from_secrets_dict({str(k): str(v) for k, v in raw.items()})
            ho = ho or bool((o or "").strip())
            ha = ha or bool((a or "").strip())
            hg = hg or bool((g or "").strip())
    return ho, ha, hg


def user_stored_classifier_api_key_presence(session: Session, user_id: str) -> tuple[bool, bool, bool]:
    """Return (openai, anthropic, google) flags from encrypted ``UserSecrets`` JSON only.

    Ignores process environment — matches what the dashboard wizard saves/removes via
    ``POST /api/onboarding/api-key``. Use this for ``GET /classifier-status`` so local ``.env``
    keys do not look like “user pasted keys”, and removal updates the payload immediately.
    """
    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == user_id)).first()
    if not row or not row.secrets_json:
        return False, False, False
    try:
        raw = json.loads(row.secrets_json)
    except json.JSONDecodeError:
        return False, False, False
    if not isinstance(raw, dict):
        return False, False, False
    o, a, g = _triplet_from_secrets_dict({str(k): str(v) for k, v in raw.items()})
    return (
        bool((o or "").strip()),
        bool((a or "").strip()),
        bool((g or "").strip()),
    )


def user_has_classifier_api_key(session: Session, user_id: str) -> bool:
    """True if env **or** stored user secrets provide at least one LLM provider key."""
    ho, ha, hg = user_classifier_api_key_presence(session, user_id)
    return ho or ha or hg


def effective_onboarding_unknown_threshold(session: Session, user_id: str) -> int:
    """Budget before classification pause — import stops when unknowns **exceed** this count.

    Example: default ``20`` allows up to ``20`` pending rows for the active source; the next
    row that pushes the count to ``21`` triggers ``needs_classification``.
    Lower values apply when LLM cannot trim unknowns (no keys or ``LLM_MODEL=none``).
    """
    low = int(os.getenv("ONBOARDING_UNKNOWN_THRESHOLD_LOW", "10"))
    high = int(os.getenv("ONBOARDING_UNKNOWN_THRESHOLD", "20"))
    if str(pc.LLM_MODEL or "").strip().lower() == "none":
        return low
    if not user_has_classifier_api_key(session, user_id):
        return low
    return high


def effective_onboarding_resume_threshold(session: Session, user_id: str) -> int:
    """Resume policy for chunk import after ``POST /api/onboarding/classify``.

    Default ``0``: resume as soon as the review queue is empty. Set
    ``ONBOARDING_RESUME_THRESHOLD`` to a positive integer for hysteresis (resume only
    while unknowns stay strictly below that count).

    ``session`` / ``user_id`` are reserved for future per-user tuning.
    """
    _ = session, user_id
    return int(os.getenv("ONBOARDING_RESUME_THRESHOLD", "0"))


def onboarding_should_resume_after_classify(
    remaining_unknowns: int, resume_threshold: int
) -> bool:
    """Whether the client should POST backfill with ``resume_after_classification``.

    * ``resume_threshold <= 0`` — resume only when ``remaining_unknowns == 0``.
    * ``resume_threshold > 0`` — resume while ``remaining_unknowns < resume_threshold``.
    """
    rt = int(resume_threshold)
    if rt <= 0:
        return int(remaining_unknowns) == 0
    return int(remaining_unknowns) < rt


@contextmanager
def user_classifier_runtime(session: Session, user_id: str) -> Iterator[None]:
    """Temporarily overlay ``pipeline.config`` LLM keys from ``UserSecrets``.

    Commits the ORM session after the ``UserSecrets`` read (before ``yield``) so the
    transaction opened by that query is not held across slow LLM HTTP calls.

    Callers that perform **additional** database reads after entering this context
    (e.g. :func:`~api.services.user_classification.pipeline_config_for_account_owner`
    in the email scraper) must ``session.commit()`` again after those reads and before
    :func:`~pipeline.llm_classifier.classify_llm` so SQLite stays responsive to
    concurrent API writes.
    """
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
        # End the autobegin transaction from the SELECT above; callers run LLM work after yield.
        session.commit()
        yield
    finally:
        pc.OPENAI_API_KEY, pc.ANTHROPIC_API_KEY, pc.GOOGLE_API_KEY = snap_o, snap_a, snap_g
